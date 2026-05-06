-- position.lua - read a session's queue position and the queue's total size
-- KEYS[1] = queue sorted set
-- ARGV[1] = session id
-- Returns: {position, size}. position is 1-indexed, 0 if not in queue.

local rank = redis.call('ZRANK', KEYS[1], ARGV[1])
local size = redis.call('ZCARD', KEYS[1])
if rank == false then
  return { 0, size }
end
return { rank + 1, size }
