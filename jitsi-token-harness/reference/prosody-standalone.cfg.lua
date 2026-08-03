-- Phase one harness: Jitsi's real multi-tenancy layout.
-- ONE virtual host and ONE muc component; tenants are expressed by addressing
-- conference.<tenant>.<base>, which muc_domain_mapper rewrites to
-- [<tenant>]room@conference.<base>. token_verification then compares the
-- subdomain it recovers against the token's sub claim.
package.path = "/opt/jitsi-plugins/?.lua;" .. package.path

plugin_paths = { "/opt/jitsi-plugins" }
admins = { }
daemonize = false
modules_enabled = { "disco"; "saslauth"; "tls"; "ping"; "bosh"; "jitsi_session"; }
modules_disabled = { "s2s" }
allow_registration = false
c2s_require_encryption = false
authentication = "internal_hashed"

consider_bosh_secure = true
cross_domain_bosh = true
http_ports = { 5280 }
http_interfaces = { "127.0.0.1" }
log = { info = "*console"; }
data_path = "/var/lib/prosody"

-- The option token/util.lib.lua needs to recover the tenant from a room JID.
muc_mapper_domain_base = "meet.jitsi";

VirtualHost "meet.jitsi"
        authentication = "token"
        app_id = "zulip"
        app_secret = "<JWT_APP_SECRET from .env>"
        allow_empty_token = false
        asap_accepted_issuers = { "zulip" }
        asap_accepted_audiences = { "jitsi" }

Component "conference.meet.jitsi" "muc"
        modules_enabled = { "token_verification"; "token_no_wildcard"; "muc_domain_mapper"; "phase1_rooms"; }
        phase1_rooms = {
            "[engineering]c-phase1-probe";
            "[engineering]c-phase1-probe-other";
            "[design]c-phase1-probe";
        }
        restrict_room_creation = false
        muc_room_locking = false
        muc_room_default_public_jids = true
