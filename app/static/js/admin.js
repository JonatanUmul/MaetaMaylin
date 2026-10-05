(() => {
  'use strict';
  const input = document.getElementById('photo-input');
  const preview = document.getElementById('photo-preview');
  let objectUrl;
  input?.addEventListener('change', () => {
    const photo = input.files[0];
    if (!photo || !preview) return;
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = URL.createObjectURL(photo);
    preview.src = objectUrl; preview.hidden = false;
    const placeholder = document.getElementById('photo-placeholder');
    if (placeholder) placeholder.hidden = true;
  });
  document.getElementById('photo-url')?.addEventListener('change', event => {
    if (input?.files.length || !preview) return;
    const value = event.target.value.trim();
    if (/^https?:\/\//.test(value) || value.startsWith('/static/')) {
      preview.src = value; preview.hidden = false;
      const placeholder = document.getElementById('photo-placeholder');
      if (placeholder) placeholder.hidden = true;
    }
  });
})();
