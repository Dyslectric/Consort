# Deployment-friendly repackaging of the calls endpoint.
#
# The canonical patch adds create_jitsi_call (and its helpers) to
# zerver/views/video_calls.py. For hand-applying to a running docker-zulip
# container we keep it self-contained here so the only edit to an existing
# Zulip file is registering the route in zproject/urls.py. The logic is
# identical to the patch; only the file it lives in differs.
#
# Drop this at zerver/views/jitsi_calls.py inside the container.

import logging
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.core.signing import BadSignature, Signer
from django.http import HttpRequest, HttpResponse
from django.utils.translation import gettext as _
from pydantic import Json

from zerver.lib.exceptions import JsonableError
from zerver.lib.jitsi_token import (
    build_user_context,
    channel_scope,
    derive_room_name,
    direct_message_scope,
    jitsi_jwt_is_configured,
    mint_jitsi_token,
)
from zerver.lib.response import json_success
from zerver.lib.streams import access_stream_by_id
from zerver.lib.typed_endpoint import typed_endpoint
from zerver.lib.url_encoding import append_url_query_string
from zerver.lib.user_groups import is_user_in_group
from zerver.lib.users import access_user_by_id
from zerver.models import NamedUserGroup, UserProfile
from zerver.views.video_calls import VideoCallProviderNotConfiguredError

logger = logging.getLogger(__name__)


def notify_conferencing_service(
    user: UserProfile,
    *,
    scope: str,
    room: str,
    tenant: str,
    stream_id: int | None,
    user_ids: list[int] | None = None,
) -> None:
    """Best-effort: tell the conferencing service a call was minted.

    The service owns the call message and the live occupancy roster, but it
    cannot map a room name — a one-way HMAC — back to a conversation. The mint is
    the one place that knows, so it tells the service here.

    Everything is swallowed: a slow or down service must never break call
    creation. The call still works; it just will not get a roster message.

    Both channel and direct-message calls are notified now. Under the core-hook
    design the service posts through Zulip's internal send path (jitsi_hook.py),
    so a DM/group message is authored as the initiator and lands in the real
    conversation — the thing the old bot could not do. For a DM we send the full
    participant set (including the initiator) so the service can post it and,
    later, answer the occupancy widget by DM scope.
    """
    url = getattr(settings, "JITSI_CONFERENCING_URL", None)
    if not url:
        return
    participants = None
    if stream_id is None:
        # A direct-message call: carry the whole participant set, initiator
        # included, sorted for stability. The service hands this to the hook,
        # which authors as the initiator.
        participants = sorted(set(user_ids or []) | {user.id})
    payload = {
        "room": room,
        "tenant": tenant,
        "scope": scope,
        "realm_id": user.realm_id,
        "realm_subdomain": user.realm.subdomain,
        "stream_id": stream_id,
        "user_ids": participants,
        "initiator_id": user.id,
        "initiator_name": user.full_name,
        "topic": getattr(settings, "JITSI_CALL_TOPIC", "Calls"),
    }
    try:
        requests.post(
            url.rstrip("/") + "/api/v1/jitsi/calls/created",
            json=payload,
            headers={"Authorization": f"Bearer {getattr(settings, 'JITSI_CONFERENCING_SECRET', '')}"},
            # Bypass the outbound SSRF proxy (smokescreen). Zulip routes app HTTP
            # through it via http_proxy in the worker env, and it blocks private
            # IPs — so without this the POST to the internal conferencing service
            # is silently eaten (a proxy block response, not an exception, so it
            # neither arrives nor raises). This is a trusted internal target.
            proxies={"http": None, "https": None},
            timeout=2,
        )
    except Exception:
        # A production version should hand this to a queue worker rather than
        # block the request; for now a short timeout plus this swallow is the
        # cost of the service being unreachable.
        logger.warning("could not notify conferencing service of call in %s", room, exc_info=True)


def resolve_jitsi_tenant(user: UserProfile) -> str:
    """Map a user to a Jitsi tenant.

    Multi-org deployment: tenant == the realm subdomain. The root org's
    subdomain is the empty string, which is not a valid tenant path segment, so
    it falls back to the slug "root".
    """
    if settings.JITSI_TENANT_BY_GROUP:
        for group_name in sorted(settings.JITSI_TENANT_BY_GROUP):
            try:
                group = NamedUserGroup.objects.get(
                    name=group_name, realm=user.realm, is_system_group=False
                )
            except NamedUserGroup.DoesNotExist:
                continue
            if is_user_in_group(group.id, user):
                return settings.JITSI_TENANT_BY_GROUP[group_name].lower()

    if settings.JITSI_DEFAULT_TENANT is not None:
        return settings.JITSI_DEFAULT_TENANT.lower()
    return user.realm.subdomain.lower() or "root"


EPOCH_SIGNER_SALT = "zerver.views.video_calls.jitsi_epoch"


def sign_jitsi_epoch(scope: str, epoch: int) -> str:
    return Signer(salt=EPOCH_SIGNER_SALT).sign_object({"scope": scope, "epoch": epoch})


def unsign_jitsi_epoch(scope: str, epoch_token: str | None) -> int:
    if epoch_token is None:
        return 0
    try:
        data = Signer(salt=EPOCH_SIGNER_SALT).unsign_object(epoch_token)
    except BadSignature:
        raise JsonableError(_("Invalid epoch token"))
    if not isinstance(data, dict) or data.get("scope") != scope:
        raise JsonableError(_("Invalid epoch token"))
    epoch = data.get("epoch")
    if not isinstance(epoch, int) or epoch < 0:
        raise JsonableError(_("Invalid epoch token"))
    return epoch


@typed_endpoint
def create_jitsi_call(
    request: HttpRequest,
    user: UserProfile,
    *,
    stream_id: Json[int] | None = None,
    user_ids: Json[list[int]] | None = None,
    epoch_token: str | None = None,
    rotate: Json[bool] = False,
) -> HttpResponse:
    if settings.JITSI_SERVER_URL is None:
        raise VideoCallProviderNotConfiguredError("Jitsi Meet")
    if not jitsi_jwt_is_configured():
        raise VideoCallProviderNotConfiguredError("Jitsi Meet (JWT)")

    if (stream_id is None) == (user_ids is None):
        raise JsonableError(_("Specify exactly one of stream_id or user_ids"))

    is_moderator = user.is_realm_admin
    if stream_id is not None:
        stream, sub = access_stream_by_id(user, stream_id)
        if sub is None:
            raise JsonableError(_("Not subscribed to this channel"))
        scope = channel_scope(user.realm_id, stream.id)
        is_moderator = is_moderator or is_user_in_group(
            stream.can_administer_channel_group_id, user
        )
    else:
        assert user_ids is not None
        for user_id in set(user_ids) - {user.id}:
            access_user_by_id(user, user_id, allow_bots=True, for_admin=False)
        scope = direct_message_scope(user.realm_id, [*user_ids, user.id])

    epoch = unsign_jitsi_epoch(scope, epoch_token)
    if rotate:
        epoch += 1

    room = derive_room_name(scope, epoch)
    tenant = resolve_jitsi_tenant(user)

    token = mint_jitsi_token(
        tenant=tenant,
        room=room,
        group=tenant,
        user_context=build_user_context(
            user_id=user.id,
            full_name=user.full_name,
            email=user.delivery_email if user.email_address_is_realm_public() else "",
            is_moderator=is_moderator,
        ),
    )

    base_url = user.realm.jitsi_server_url or settings.JITSI_SERVER_URL
    url = f"{base_url.rstrip('/')}/{tenant}/{room}"

    notify_conferencing_service(
        user, scope=scope, room=room, tenant=tenant, stream_id=stream_id, user_ids=user_ids
    )

    return json_success(
        request,
        {
            "url": append_url_query_string(url, urlencode({"jwt": token})),
            "room": room,
            "tenant": tenant,
            "epoch_token": sign_jitsi_epoch(scope, epoch),
        },
    )


@typed_endpoint
def get_jitsi_occupancy(
    request: HttpRequest,
    user: UserProfile,
    *,
    stream_id: Json[int],
) -> HttpResponse:
    """Occupancy of a channel's live call, for the presence widget.

    Runs as the user, so it enforces the same channel access the call endpoint
    does before revealing who is in a call — `access_stream_by_id` raises unless
    the user can reach the channel. The conferencing service holds the occupancy;
    this proxies to it and never trusts the browser with the service's address or
    secret. `proxies={}` bypasses the SSRF proxy (smokescreen) for this trusted
    internal target, the same reason `notify` does.

    Best-effort: if the service is unreachable, report an empty, inactive call
    rather than erroring — a missing widget is better than a broken compose box.
    """
    access_stream_by_id(user, stream_id)  # entitlement: raises if no access

    empty = {"stream_id": stream_id, "active": False, "count": 0, "occupants": [], "drifted": False}
    url = getattr(settings, "JITSI_CONFERENCING_URL", None)
    if not url:
        return json_success(request, empty)
    try:
        response = requests.get(
            url.rstrip("/") + "/api/v1/jitsi/occupancy",
            params={"stream_id": stream_id},
            headers={
                "Authorization": f"Bearer {getattr(settings, 'JITSI_CONFERENCING_SECRET', '')}"
            },
            proxies={"http": None, "https": None},
            timeout=2,
        )
        data = response.json()
    except Exception:
        logger.warning("could not fetch occupancy for channel %s", stream_id, exc_info=True)
        return json_success(request, empty)
    return json_success(request, data)
