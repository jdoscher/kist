// First paint in the stored theme and density, before the app loads (an
// external file because the CSP allows no inline script). Same rules as
// resolveTheme() in src/theme.ts; the background is THEME_BG there (each
// theme's --bg), so the first frame is already the theme's colour.
;(function () {
  var theme = 'system'
  var density = 'comfortable'
  try {
    theme = localStorage.getItem('kist.theme') || theme
    density = localStorage.getItem('kist.density') || density
  } catch (e) {}
  var explicit = theme === 'light' || theme === 'dark' || theme === 'gallery'
  if (!explicit) {
    theme = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  }
  if (density !== 'compact' && density !== 'gallery') density = 'comfortable'
  var root = document.documentElement
  root.setAttribute('data-theme', theme)
  root.setAttribute('data-density', density)
  root.style.colorScheme = theme === 'light' ? 'light' : 'dark'
  root.style.backgroundColor = theme === 'gallery' ? '#5C5C5C' : theme === 'light' ? '#FAF9F6' : '#131312'
  // the browser bar matches the top bar from the first frame (THEME_COLOR;
  // index.html puts the theme-color metas above this script). An explicit
  // theme pins both metas, as applyAppearance() does; System leaves them.
  if (explicit) {
    var bar = theme === 'gallery' ? '#5C5C5C' : theme === 'light' ? '#FFFFFF' : '#1C1C1A'
    var metas = document.querySelectorAll('meta[name="theme-color"]')
    for (var i = 0; i < metas.length; i++) {
      metas[i].setAttribute('data-default', metas[i].getAttribute('content'))
      metas[i].setAttribute('content', bar)
    }
  }
})()
