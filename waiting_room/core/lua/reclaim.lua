-- reclaim.lua - drop expired sessions from the queue and admitted set
-- KEYS[1] = queue sorted set
-- KEYS[2] = admitted sorted set
-- ARGV[1] = queue cutoff score (sessions enqueued before this are abandoned)
-- ARGV[2] = admitted cutoff score (admissions whose grace expired)
-- Returns: total reclaimed count.

local q = redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
local a = redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', ARGV[2])
return q + a
