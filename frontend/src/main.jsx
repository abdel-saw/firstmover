if (new URLSearchParams(window.location.search).get('app') === 'caseweave') {
  import('./caseweave.jsx');
} else {
  import('./firstmover.jsx');
}
