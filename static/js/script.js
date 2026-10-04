document.addEventListener('DOMContentLoaded', function () {
    const timerEl = document.getElementById('timer');
    const createdEl = document.querySelector('[data-created]');

    if (timerEl && createdEl) {
        const createdAt = new Date(createdEl.dataset.created);
        const deadline = new Date(createdAt.getTime() + 8 * 60 * 1000);

        function updateTimer() {
            const now = new Date();
            const remaining = Math.max(0, deadline - now);
            const minutes = Math.floor(remaining / 60000);
            const seconds = Math.floor((remaining % 60000) / 1000);
            timerEl.textContent = `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;

            if (remaining <= 0) {
                clearInterval(timerInterval);
            }
        }

        updateTimer();
        const timerInterval = setInterval(updateTimer, 1000);
    }

    const refreshBtn = document.querySelector('.captcha-refresh');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', function () {
            const captcha = document.getElementById('captcha-display');
            if (captcha) {
                const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
                let value = '';
                for (let i = 0; i < 6; i++) {
                    value += chars[Math.floor(Math.random() * chars.length)];
                }
                captcha.textContent = value;
            }
        });
    }
});
