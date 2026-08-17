document.addEventListener('DOMContentLoaded', () => {
    // Theme logic
    const themeToggleBtn = document.getElementById('theme-toggle');
    const htmlElement = document.documentElement;
    const savedTheme = localStorage.getItem('theme');
    
    if (savedTheme) {
        htmlElement.setAttribute('data-theme', savedTheme);
        updateToggleIcon(savedTheme);
    } else {
        htmlElement.setAttribute('data-theme', 'dark');
        updateToggleIcon('dark');
    }

    if (themeToggleBtn) {
        themeToggleBtn.addEventListener('click', () => {
            const currentTheme = htmlElement.getAttribute('data-theme');
            const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
            
            htmlElement.setAttribute('data-theme', newTheme);
            localStorage.setItem('theme', newTheme);
            updateToggleIcon(newTheme);
        });
    }

    function updateToggleIcon(theme) {
        if (!themeToggleBtn) return;
        const iconSpan = themeToggleBtn.querySelector('.icon');
        if (theme === 'dark') {
            iconSpan.textContent = '☀️';
            iconSpan.setAttribute('aria-label', 'Switch to light mode');
        } else {
            iconSpan.textContent = '🌙';
            iconSpan.setAttribute('aria-label', 'Switch to dark mode');
        }
    }

    // Notifications logic
    const notifBtn = document.getElementById('notif-btn');
    const notifDropdown = document.getElementById('notif-dropdown');
    
    if (notifBtn && notifDropdown) {
        notifBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            notifDropdown.classList.toggle('hidden');
        });

        // Close when clicking outside
        document.addEventListener('click', (e) => {
            if (!notifDropdown.contains(e.target) && !notifBtn.contains(e.target)) {
                notifDropdown.classList.add('hidden');
            }
        });

        // Mark as read
        document.querySelectorAll('.mark-read-btn').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                e.preventDefault();
                e.stopPropagation();
                
                const notifItem = e.target.closest('.notif-item');
                const notifId = notifItem.dataset.id;
                
                try {
                    const response = await fetch(`/notifications/read/${notifId}`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        }
                    });
                    
                    if (response.ok) {
                        notifItem.remove();
                        // Update badge count
                        const badge = document.querySelector('.notif-badge');
                        if (badge) {
                            const newCount = parseInt(badge.textContent) - 1;
                            if (newCount > 0) {
                                badge.textContent = newCount;
                            } else {
                                badge.remove();
                            }
                        }
                    }
                } catch (err) {
                    console.error('Failed to mark read', err);
                }
            });
        });
        
        // Mark all as read
        const readAllBtn = document.getElementById('read-all-btn');
        if (readAllBtn) {
            readAllBtn.addEventListener('click', async (e) => {
                e.preventDefault();
                e.stopPropagation();
                try {
                    const response = await fetch('/notifications/read_all', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        }
                    });
                    
                    if (response.ok) {
                        document.querySelectorAll('.notif-item').forEach(item => item.remove());
                        const badge = document.querySelector('.notif-badge');
                        if (badge) badge.remove();
                        readAllBtn.remove();
                        const list = document.querySelector('.notif-list');
                        if (list) {
                            list.innerHTML = `<div class="notif-empty">No new notifications</div>`;
                        }
                    }
                } catch (err) {
                    console.error('Failed to mark all read', err);
                }
            });
        }
    }
});
