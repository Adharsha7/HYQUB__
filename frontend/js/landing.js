/* HYQUB Landing Page - Vanilla Interactions */
document.documentElement.classList.add('js');

document.addEventListener('DOMContentLoaded', () => {
  const isTouch = window.matchMedia('(pointer: coarse)').matches;
  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (prefersReduced) document.documentElement.classList.add('reduced-motion');

  // 1. Mobile Menu Toggle
  const toggle = document.getElementById('mobile-toggle');
  const menu = document.getElementById('mobile-nav');
  if (toggle && menu) {
    toggle.addEventListener('click', () => {
      const open = toggle.getAttribute('aria-expanded') === 'true';
      toggle.setAttribute('aria-expanded', String(!open));
      menu.hidden = open;
    });
    menu.querySelectorAll('a').forEach((link) => {
      link.addEventListener('click', () => {
        toggle.setAttribute('aria-expanded', 'false');
        menu.hidden = true;
      });
    });
  }

  // 2. IntersectionObserver for Scroll Fade-ins
  if ('IntersectionObserver' in window && !prefersReduced) {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('visible');
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.1 });
    document.querySelectorAll('.animate-on-scroll').forEach((el) => observer.observe(el));
  }

  // 3. About Text Scroll Word Reveal
  const words = document.querySelectorAll('#about-text .word');
  const aboutSec = document.getElementById('about');
  function updateAboutWords() {
    if (!words.length || !aboutSec || prefersReduced) return;
    const rect = aboutSec.getBoundingClientRect();
    const winH = window.innerHeight;
    const progress = Math.max(0, Math.min(1, (winH * 0.85 - rect.top) / (winH * 0.5)));
    words.forEach((w, idx) => {
      w.style.opacity = progress >= (idx / words.length) ? '1' : '0.2';
    });
  }

  // 4. Stacking Cards Scale Smoothing on Scroll
  const stackWraps = document.querySelectorAll('.sec-card-wrapper');
  function updateStackingCards() {
    if (!stackWraps.length || prefersReduced) return;
    stackWraps.forEach((wrap, i) => {
      const card = wrap.querySelector('.sec-card');
      if (!card || i === stackWraps.length - 1) return;
      const rect = wrap.getBoundingClientRect();
      const targetScale = i === 0 ? 0.94 : 0.97;
      if (rect.top <= (96 + i * 24)) {
        const factor = Math.min(1, Math.max(0, (96 + i * 24 - rect.top) / (window.innerHeight * 0.5)));
        card.style.transform = `scale(${(1 - (1 - targetScale) * factor).toFixed(3)})`;
      } else {
        card.style.transform = 'scale(1)';
      }
    });
  }

  // Combined Scroll Listener (rAF throttled + passive)
  let scrollTicking = false;
  window.addEventListener('scroll', () => {
    if (!scrollTicking && !prefersReduced) {
      scrollTicking = true;
      requestAnimationFrame(() => {
        updateAboutWords();
        updateStackingCards();
        scrollTicking = false;
      });
    }
  }, { passive: true });
  updateAboutWords();

  // 5. Magnet Effect on Hero Shield Art
  const magnetEl = document.getElementById('shield-magnet');
  const magnetArea = document.getElementById('shield-magnet-area');
  let magnetActive = false;
  if (magnetEl && magnetArea && !isTouch && !prefersReduced) {
    let mouseX = 0, mouseY = 0, mouseTicking = false;
    window.addEventListener('mousemove', (e) => {
      mouseX = e.clientX;
      mouseY = e.clientY;
      if (!mouseTicking) {
        mouseTicking = true;
        requestAnimationFrame(() => {
          mouseTicking = false;
          const rect = magnetArea.getBoundingClientRect();
          const centerX = rect.left + rect.width / 2;
          const centerY = rect.top + rect.height / 2;
          const distX = Math.max(0, rect.left - mouseX, mouseX - rect.right);
          const distY = Math.max(0, rect.top - mouseY, mouseY - rect.bottom);
          if (Math.hypot(distX, distY) <= 150) {
            magnetActive = true;
            magnetEl.style.transition = 'transform 0.3s ease-out';
            magnetEl.style.transform = `translate3d(${((mouseX - centerX) / 3).toFixed(1)}px, ${((mouseY - centerY) / 3).toFixed(1)}px, 0)`;
          } else if (magnetActive) {
            magnetActive = false;
            magnetEl.style.transition = 'transform 0.6s ease-in-out';
            magnetEl.style.transform = 'translate3d(0, 0, 0)';
          }
        });
      }
    }, { passive: true });
  }
});
