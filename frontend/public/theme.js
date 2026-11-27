try {
  const { dataset } = document.documentElement
  if (localStorage.getItem(dataset.themeStorageKey) === 'dark') {
    dataset.theme = 'dark'
  }
} catch {
  // The default theme works when browser storage is unavailable.
}
