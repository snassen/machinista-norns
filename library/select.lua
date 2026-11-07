-- Script chooser with folders. Replaces lua/core/menu/select.lua.
--
-- Folders are virtual: scripts stay where they are installed, and data/script-library.lua
-- says which folder each one belongs in. Anything it doesn't know goes in "other".
--
-- K3 opens a folder or previews a script. K2 goes back. E2 moves, E3 adds or removes a
-- favorite, and K1 held makes E2 move in larger steps.
local tabutil = require "tabutil"
local LIBRARY = _path.data .. "script-library.lua"
local DESCRIPTION_CHARS = 31

local m = {
  pos = 0,
  list = {},
  scripts = {},
  favorites = {},
  len = "scan",
  alt = false,
  folder = nil,   -- the open folder's name, nil at the top level
  top_pos = 0,    -- where to return to when leaving a folder
  scroll = 0,
}

local function load_library()
  local ok, library = pcall(dofile, LIBRARY)
  if ok and type(library) == "table" then return library end
  return { folders = {}, scripts = {} }
end

local function menu_table_entry(file, library)
  local p = string.match(file,".*/")
  local n = string.gsub(file,'.lua','/')
  n = string.gsub(n,_path.code,'')
  n = string.sub(n,0,-2)
  local a,b = string.match(n,"(.+)/(.+)$") -- strip similar dir/script
  if a==b and a then n = a end
  local info = library.scripts[string.match(n, "^[^/]+")] or {}
  return { name=n, file=file, path=p, folder=info.folder or "other", description=info.description }
end

local function contains(list, menu_item)
  for _, v in pairs(list) do
    if v.file == menu_item.file then
      return true
    end
  end
  return false
end

-- what the screen lists right now
local function rebuild()
  m.list = {}
  if m.folder then
    for _, script in ipairs(m.scripts) do
      if script.folder == m.folder then table.insert(m.list, script) end
    end
  else
    for _, entry in pairs(m.favorites) do table.insert(m.list, entry) end
    if #m.list > 0 then table.insert(m.list, { name="-" }) end
    local counts = {}
    for _, script in ipairs(m.scripts) do
      counts[script.folder] = (counts[script.folder] or 0) + 1
    end
    for _, name in ipairs(m.folder_order) do
      if counts[name] then
        table.insert(m.list, { name=name, opens=name, count=counts[name] })
        counts[name] = nil
      end
    end
  end
  m.len = #m.list
  m.pos = util.clamp(m.pos, 0, math.max(m.len - 1, 0))
  if m.list[m.pos + 1] and m.list[m.pos + 1].name == "-" and m.pos + 1 < m.len then m.pos = m.pos + 1 end
end

local function selected() return m.list[m.pos + 1] end

local function sort_select_tree(results)
  local library = load_library()
  m.folder_order = {}
  for _, name in ipairs(library.folders) do table.insert(m.folder_order, name) end
  if not tab.contains(m.folder_order, "other") then table.insert(m.folder_order, "other") end
  m.scripts = {}
  for filename in results:gmatch("[^\r\n]+") do
    table.insert(m.scripts, menu_table_entry(_path.code .. filename, library))
  end
  rebuild()
  _menu.redraw()
end

m.init = function()
  m.len = "scan"
  m.list = {}
  m.favorites = tabutil.load(_path.favorites)
  if m.favorites == nil then
    m.favorites = {}
    tabutil.save(m.favorites, _path.favorites)
  end
  -- weird command, but it is fast, recursive, skips hidden dirs, and sorts
  norns.system_cmd('find ~/dust/code/ -mindepth 2 ' ..
                   '-name .git -prune -o -type f -name "*.lua" -printf "%P\n" | ' ..
                   'grep -Ev "/(lib|data|crow|test|docs)/" | ' ..
                   'sort',
                   sort_select_tree)
  _menu.timer.time = 0.25
  _menu.timer.count = -1
  _menu.timer.event = function()
    local item = m.len ~= "scan" and selected()
    if item and item.description then
      m.scroll = m.scroll + 1
      _menu.redraw()
    end
  end
  _menu.timer:start()
end

m.deinit = function()
  _menu.timer:stop()
end

m.key = function(n,z)
  if n == 1 then
    m.alt = z == 1 and true or false
  elseif n==2 and z==1 then
    if m.folder then
      m.folder = nil
      m.pos = m.top_pos
      m.scroll = 0
      rebuild()
      _menu.redraw()
    else
      _menu.set_page("HOME")
    end
  elseif n==3 and z==1 then
    local item = selected()
    if m.len == "scan" or item == nil or item.name == "-" then return end
    if item.opens then
      m.top_pos = m.pos
      m.folder = item.opens
      m.pos = 0
      m.scroll = 0
      rebuild()
      _menu.redraw()
    elseif util.file_exists(item.file) then
      _menu.previewfile = item.file
      _menu.set_page("PREVIEW")
    else
      m.remove_favorite()
      screen.clear()
      screen.level(15)
      screen.move(64,40)
      screen.text_center("script not found")
      screen.update()
    end
  end
end

m.enc = function(n,delta)
  if m.len == "scan" then return end
  if n==2 then
    delta = not m.alt and delta or delta*6
    m.pos = util.clamp(m.pos + delta, 0, math.max(m.len - 1, 0))
    m.scroll = 0
    _menu.redraw()
  elseif n==3 then
    if delta > 0 then
      m.add_favorite()
    else
      m.remove_favorite()
    end
    _menu.redraw()
  end
end

local function draw_description(item)
  -- Plain ASCII only: cutting UTF-8 mid-character puts cairo in an error state, and then
  -- nothing can be drawn on the screen until norns restarts.
  local text = item.description and string.gsub(item.description, "[\128-\255]", "")
  if not text or text == "" then return end
  if #text > DESCRIPTION_CHARS then
    -- rest on the first words, then scroll round
    local offset = math.max(m.scroll - 8, 0) % (#text + 4)
    text = string.sub(text .. "    " .. text, offset + 1, offset + DESCRIPTION_CHARS)
  end
  screen.level(3)
  screen.move(0, 62)
  screen.text(text)
end

m.redraw = function()
  screen.clear()
  screen.level(15)
  if m.len == "scan" then
    screen.move(64,40)
    screen.text_center("scanning...")
  elseif m.len == 0 then
    screen.move(64,40)
    screen.text_center("no files")
  else
    for i=1,5 do
      local item = m.list[i+m.pos-2]
      if item then
        screen.move(0,10*i)
        screen.level(i==3 and 15 or 4)
        if item.opens then
          screen.text(string.upper(item.name) .. " >")
          screen.move(127,10*i)
          screen.text_right(item.count)
        elseif item.name == "-" then
          screen.text("-")
        else
          local is_fave = contains(m.favorites, item) and "* " or "  "
          screen.text(is_fave .. string.upper(item.name))
        end
      end
    end
    local item = selected()
    if item then draw_description(item) end
  end
  screen.update()
end

m.add_favorite = function()
  local item = selected()
  if item == nil or item.file == nil then return end
  if not contains(m.favorites, item) then
    table.insert(m.favorites, item)
    tabutil.save(m.favorites, _path.favorites)
    if not m.folder then rebuild() end
  end
end

m.remove_favorite = function()
  local item = selected()
  if item == nil or item.file == nil then return end
  for i, v in pairs(m.favorites) do
    if v.file == item.file then
      table.remove(m.favorites, i)
      tabutil.save(m.favorites, _path.favorites)
      if not m.folder then rebuild() end
      return
    end
  end
end

return m
