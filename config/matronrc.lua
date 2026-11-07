-- A headless desktop configuration. Cairo remains the authoritative framebuffer;
-- the browser asks norns to export it through _norns.screen_export_png().
function init_desktop()
  -- OSC virtual controls and the stdin REPL provide all input in this runtime.
end

init_desktop()
