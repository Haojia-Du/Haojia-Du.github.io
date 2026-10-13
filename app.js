const root = document.documentElement;
const themeButton = document.querySelector('#theme-toggle');
const themeColor = document.querySelector('meta[name="theme-color"]');
const storedTheme = localStorage.getItem('haled-theme');
const preferredDark = window.matchMedia('(prefers-color-scheme: dark)').matches;

function applyTheme(theme) {
  root.dataset.theme = theme;
  themeColor.setAttribute('content', theme === 'dark' ? '#0b0d10' : '#f8f9fb');
}

applyTheme(storedTheme || (preferredDark ? 'dark' : 'light'));

themeButton.addEventListener('click', () => {
  const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
  applyTheme(next);
  localStorage.setItem('haled-theme', next);
});

const header = document.querySelector('#site-header');
const updateHeader = () => header.classList.toggle('scrolled', window.scrollY > 24);
updateHeader();
window.addEventListener('scroll', updateHeader, { passive: true });

const menuButton = document.querySelector('#menu-toggle');
const nav = document.querySelector('#nav');
menuButton.addEventListener('click', () => {
  const open = menuButton.getAttribute('aria-expanded') !== 'true';
  menuButton.setAttribute('aria-expanded', String(open));
  nav.classList.toggle('open', open);
});
nav.querySelectorAll('a').forEach(link => link.addEventListener('click', () => {
  nav.classList.remove('open');
  menuButton.setAttribute('aria-expanded', 'false');
}));

const observer = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      entry.target.classList.add('visible');
      observer.unobserve(entry.target);
    }
  });
}, { threshold: 0.12 });
document.querySelectorAll('.reveal').forEach(el => observer.observe(el));

document.querySelector('#year').textContent = new Date().getFullYear();

const copyButton = document.querySelector('#copy-link');
copyButton.addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(window.location.href.split('#')[0]);
    const label = copyButton.querySelector('span');
    label.textContent = '已复制';
    copyButton.classList.add('success');
    window.setTimeout(() => {
      label.textContent = '复制本站链接';
      copyButton.classList.remove('success');
    }, 1800);
  } catch {
    window.prompt('复制本站链接：', window.location.href.split('#')[0]);
  }
});

