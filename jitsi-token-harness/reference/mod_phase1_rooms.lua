-- Substitute for Jicofo, for harness use only.
--
-- Jitsi's token_util:verify_room() returns 'room-does-not-exist' before it ever
-- compares the token's room/sub claims. In a real deployment Jicofo (which is in
-- token_verification's allowlist) creates the room, and participants join an
-- existing one. Without a focus component, every probe would be refused for a
-- reason that has nothing to do with the token — and, worse, the cross-room case
-- would "pass" without the room claim ever being checked.
--
-- Rooms are listed with their MAPPED names, i.e. after muc_domain_mapper has
-- rewritten conference.<tenant>.<base> into [<tenant>]room@conference.<base>.
local rooms = module:get_option_array("phase1_rooms", {});
local mod_muc = module:depends("muc");

module:hook_global("server-started", function()
    for _, name in ipairs(rooms) do
        local jid = name .. "@" .. module.host;
        local room = mod_muc.get_room_from_jid(jid);
        if not room then
            room = mod_muc.create_room(jid);
            module:log("info", "phase1: pre-created room %s", jid);
        end
        if room then
            room:set_persistent(true);
        end
    end
end, -1000);
