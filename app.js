let theme;try{theme=localStorage.getItem('haled-blog-theme')}catch{}
document.body.classList.toggle('dark',theme==='dark');
document.querySelector('#theme-toggle').addEventListener('click',()=>{const dark=document.body.classList.toggle('dark');try{localStorage.setItem('haled-blog-theme',dark?'dark':'light')}catch{}});
document.querySelector('#year').textContent=new Date().getFullYear();
document.querySelectorAll('[data-category]').forEach(button=>button.addEventListener('click',()=>{document.querySelectorAll('[data-category]').forEach(item=>{item.classList.toggle('active',item===button);item.setAttribute('aria-pressed',String(item===button))});document.querySelector('.empty h3').textContent=button.dataset.category==='全部'?'这里还没有文章':`还没有「${button.dataset.category}」文章`;}));
