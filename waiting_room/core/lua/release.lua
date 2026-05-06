-- release.lua - free an admitted slot
-- KEYS[1] = admitted sorted set
-- KEYS[2] = session hash
-- ARGV[1] = session id
-- Returns: 1 if the slot was held, 0 otherwise.

local removed = redis.call('ZREM', KEYS[1], ARGV[1])
if removed == 1 then
  redis.call('HSET', KEYS[2], 'state', 'released')
end
return removed
