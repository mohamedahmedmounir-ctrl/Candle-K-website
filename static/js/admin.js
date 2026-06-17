/* Candle K — Admin JS */
'use strict';

document.addEventListener('DOMContentLoaded', () => {
  // Sidebar toggle on mobile
  const sidebar = document.querySelector('.sidebar');
  const toggle  = document.querySelector('.sidebar-toggle');
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:49;display:none;';
  document.body.appendChild(overlay);

  toggle?.addEventListener('click', () => {
    const open = sidebar?.classList.toggle('open');
    overlay.style.display = open ? 'block' : 'none';
  });
  overlay.addEventListener('click', () => {
    sidebar?.classList.remove('open');
    overlay.style.display = 'none';
  });

  // Mark active sidebar link
  document.querySelectorAll('.sidebar__link').forEach(a => {
    if (a.href && location.pathname.startsWith(new URL(a.href).pathname) && new URL(a.href).pathname !== '/admin') {
      a.classList.add('active');
    } else if (location.pathname === '/admin' || location.pathname === '/admin/dashboard') {
      document.querySelectorAll('.sidebar__link[href*="dashboard"]').forEach(x => x.classList.add('active'));
    }
  });

  // Image upload previews
  document.querySelectorAll('.image-upload-zone').forEach(zone => {
    const input = zone.querySelector('input[type=file]');
    const previewGrid = zone.nextElementSibling;
    if (!input) return;

    zone.addEventListener('click', () => input.click());
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('dragover'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
    zone.addEventListener('drop', e => {
      e.preventDefault(); zone.classList.remove('dragover');
      handleFiles(e.dataTransfer.files, previewGrid);
    });
    input.addEventListener('change', () => handleFiles(input.files, previewGrid));
  });

  function handleFiles(files, grid) {
    if (!grid) return;
    [...files].forEach(file => {
      if (!file.type.startsWith('image/')) return;
      const reader = new FileReader();
      reader.onload = e => {
        const wrap = document.createElement('div');
        wrap.className = 'image-preview';
        wrap.innerHTML = `<img src="${e.target.result}" alt=""><div class="image-preview__actions">
          <span style="color:#fff;font-size:.72rem;">Preview</span></div>`;
        grid.appendChild(wrap);
      };
      reader.readAsDataURL(file);
    });
  }

  // Confirm deletes
  document.querySelectorAll('[data-confirm]').forEach(el => {
    el.addEventListener('click', e => {
      if (!confirm(el.dataset.confirm)) e.preventDefault();
    });
  });

  // Status select color
  const statusSel = document.querySelector('select[name=status]');
  if (statusSel) {
    const update = () => {
      statusSel.className = 'admin-select status-select-' + statusSel.value;
    };
    statusSel.addEventListener('change', update);
    update();
  }
});
