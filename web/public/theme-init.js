// First paint in the stored theme and density, before the app loads (an
// external file because the CSP allows no inline script). Same rules as
// resolveTheme() in src/theme.ts.
;(function () {
  var theme = 'system'
  var density = 'comfortable'
  try {
    theme = localStorage.getItem('kist.theme') || theme
    density = localStorage.getItem('kist.density') || density
  } catch (e) {}
  if (theme !== 'light' && theme !== 'dark' && theme !== 'gallery') {
    theme = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  }
  if (density !== 'compact' && density !== 'gallery') density = 'comfortable'
  var root = document.documentElement
  root.setAttribute('data-theme', theme)
  root.setAttribute('data-density', density)
  root.style.colorScheme = theme === 'light' ? 'light' : 'dark'
})()
