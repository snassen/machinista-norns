-- machinista: lets Machinista's MK3 page through the running script's PARAMS,
-- change them, and load a script with one of its PSETs.
--
-- Machinista sends OSC to norns (port 10111) and gets answers back on its own port:
--   /machinista/page  i:page          -> /machinista/page s:payload
--   /machinista/delta s:id i:steps    -> (changes the param, then answers like /page)
--   /machinista/load  s:file i:pset   -> loads code/<file> with PSET number pset (0 = none)
--
-- The payload is text: a header line, then one line per parameter, tab separated.
--   script \t page \t pages \t section
--   id \t name \t value \t fraction (0..1, or -1 when it has no position)

local mod = require 'core/mods'
local paramset = require 'core/paramset'

local PER_PAGE = 4
local T = paramset
local USABLE = {
  [T.tNUMBER] = true, [T.tOPTION] = true, [T.tCONTROL] = true,
  [T.tTAPER] = true, [T.tTRIGGER] = true, [T.tBINARY] = true,
}

local system_count = 0   -- params that norns adds before every script (levels, clock...)
local pending_pset = nil -- the PSET to apply if the script doesn't read one itself
local pset_was_read = false

local function clean(text)
  return (tostring(text or ''):gsub('[\t\n]', ' '))
end

local function fraction(p)
  local t = p.t
  if t == T.tCONTROL or t == T.tTAPER then
    return p:get_raw()
  elseif t == T.tNUMBER then
    if p.max ~= p.min then return (p:get() - p.min) / (p.max - p.min) end
  elseif t == T.tOPTION then
    if p.count and p.count > 1 then return (p:get() - 1) / (p.count - 1) end
  elseif t == T.tBINARY then
    return p:get()
  end
  return -1
end

-- Pages of up to 4 params. A page never mixes two sections, so its title fits.
-- Script params come first, then the ones norns adds to every script.
local function pages()
  local result = {}
  local function collect(from, to, default_section)
    local section = default_section
    local page = nil
    local skip = 0
    for i = from, to do
      local p = params.params[i]
      if skip > 0 then
        skip = skip - 1
      elseif p.t == T.tSEPARATOR or p.t == T.tGROUP then
        if not params:visible(i) then
          if p.t == T.tGROUP then skip = p.n or 0 end
        else
          section = p.name or section
          page = nil
        end
      elseif USABLE[p.t] and params:visible(i) then
        if page == nil or #page.items == PER_PAGE then
          page = { section = section, items = {} }
          table.insert(result, page)
        end
        table.insert(page.items, i)
      end
    end
  end
  collect(system_count + 1, params.count, norns.state.shortname or 'script')
  collect(1, math.min(system_count, params.count), 'system')
  return result
end

local function payload(number)
  local list = pages()
  number = util.clamp(number or 1, 1, math.max(1, #list))
  local page = list[number]
  local lines = {
    table.concat({ clean(norns.state.name), number, #list, clean(page and page.section or '') }, '\t')
  }
  if page then
    for _, i in ipairs(page.items) do
      local p = params.params[i]
      table.insert(lines, table.concat({
        clean(p.id), clean(p.name), clean(params:string(i)), string.format('%.4f', fraction(p)),
      }, '\t'))
    end
  end
  return table.concat(lines, '\n')
end

local function change(id, steps)
  local i = params.lookup[id]
  if not i then return end
  local p = params.params[i]
  if p.t == T.tBINARY then
    params:set(id, steps > 0 and 1 or 0)
  elseif p.t == T.tTRIGGER then
    if steps > 0 then params:set(id, 1) end
  elseif USABLE[p.t] then
    params:delta(id, steps)
  end
end

local function load(file, pset)
  local path = _path.code .. file
  if not util.file_exists(path) then
    print('machinista: no such script ' .. path)
    return
  end
  pending_pset = (pset and pset > 0) and pset or nil
  if pending_pset then
    -- norns reads pset-last.txt on load, so a script calling params:default() gets it itself.
    -- The same data folder name that norns.script.load works out.
    local dir, script = file:match('^(.*)/([^/]*)%.lua$')
    local name = dir
    if dir:sub(-#script) ~= script then name = dir .. '/' .. script end
    local data = _path.data .. name .. '/'
    util.make_dir(data)
    local f = io.open(data .. 'pset-last.txt', 'w')
    if f then f:write(tostring(pending_pset)); f:close() end
  end
  norns.script.load(path)
end

local function handle(path, args, from)
  if path == '/machinista/page' then
    osc.send(from, '/machinista/page', { payload(tonumber(args[1])) })
  elseif path == '/machinista/delta' then
    change(args[1], tonumber(args[2]) or 0)
    osc.send(from, '/machinista/page', { payload(tonumber(args[3])) })
  elseif path == '/machinista/load' then
    load(args[1], tonumber(args[2]))
  end
end

mod.hook.register('system_post_startup', 'machinista', function()
  local original = _norns.osc.event
  _norns.osc.event = function(path, args, from)
    if util.string_starts(path, '/machinista/') then
      local ok, err = pcall(handle, path, args, from)
      if not ok then print('machinista: ' .. tostring(err)) end
      return
    end
    original(path, args, from)
  end
end)

mod.hook.register('script_pre_init', 'machinista', function()
  system_count = params.count
  pset_was_read = false
  params.read = function(self, ...)
    pset_was_read = true
    return paramset.read(self, ...)
  end
end)

mod.hook.register('script_post_init', 'machinista', function()
  if pending_pset and not pset_was_read then
    params:read(pending_pset)
  end
  pending_pset = nil
end)
