/**
 * Qanuni.AI — Authentication & Session Manager (v3.0)
 * ==================================================
 * Manages JWT tokens, guest session IDs, auth state, UI synchronization,
 * and guest conversation migration on login/signup.
 */

const Auth = {
    guestIdKey: "qanuni_guest_id",
    tokenKey: "qanuni_token",
    userKey: "qanuni_user",
    
    // Get or generate a persistent Guest Session ID
    getGuestId() {
        let guestId = localStorage.getItem(this.guestIdKey);
        if (!guestId) {
            // Generate a secure-enough random string for local usage
            const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-";
            let randStr = "";
            for (let i = 0; i < 32; i++) {
                randStr += chars.charAt(Math.floor(Math.random() * chars.length));
            }
            guestId = "guest_" + randStr;
            localStorage.setItem(this.guestIdKey, guestId);
        }
        return guestId;
    },

    getToken() {
        return localStorage.getItem(this.tokenKey);
    },

    getUser() {
        const u = localStorage.getItem(this.userKey);
        try {
            return u ? JSON.parse(u) : null;
        } catch (e) {
            return null;
        }
    },

    isLoggedIn() {
        return !!this.getToken();
    },

    saveSession(token, user) {
        localStorage.setItem(this.tokenKey, token);
        localStorage.setItem(this.userKey, JSON.stringify(user));
    },

    clearSession() {
        localStorage.removeItem(this.tokenKey);
        localStorage.removeItem(this.userKey);
    },

    async fetchUserProfile() {
        const token = this.getToken();
        if (!token) return null;

        try {
            const res = await fetch("/api/auth/me", {
                headers: {
                    "Authorization": `Bearer ${token}`
                }
            });
            if (res.ok) {
                const data = await res.json();
                localStorage.setItem(this.userKey, JSON.stringify(data.user));
                return data.user;
            } else {
                // Token might be expired or invalid
                this.clearSession();
                return null;
            }
        } catch (e) {
            console.error("[Auth] Error fetching profile:", e);
            return null;
        }
    },

    async fetchGuestUsage() {
        const guestId = this.getGuestId();
        try {
            const res = await fetch("/api/auth/guest", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ guest_id: guestId })
            });
            if (res.ok) {
                return await res.json();
            }
        } catch (e) {
            console.error("[Auth] Error fetching guest status:", e);
        }
        return null;
    },

    async init() {
        // Form tabs switching
        const tabs = document.querySelectorAll(".auth-tab");
        tabs.forEach(tab => {
            tab.onclick = () => {
                tabs.forEach(t => t.classList.remove("active"));
                tab.classList.add("active");
                
                const target = tab.dataset.tab;
                document.querySelectorAll(".auth-form").forEach(f => f.classList.remove("active"));
                document.getElementById(`${target}-form`).classList.add("active");
            };
        });

        // Close button
        const closeBtn = document.getElementById("auth-close-btn");
        if (closeBtn) {
            closeBtn.onclick = () => this.hideModal();
        }

        // Overlay click to close
        const overlay = document.getElementById("auth-modal");
        if (overlay) {
            overlay.onclick = (e) => {
                if (e.target === overlay) this.hideModal();
            };
        }

        // Form submissions
        const loginForm = document.getElementById("login-form");
        if (loginForm) {
            loginForm.onsubmit = async (e) => {
                e.preventDefault();
                await this.handleAuthSubmit("login");
            };
        }

        const signupForm = document.getElementById("signup-form");
        if (signupForm) {
            signupForm.onsubmit = async (e) => {
                e.preventDefault();
                await this.handleAuthSubmit("signup");
            };
        }

        // Check current session
        if (this.isLoggedIn()) {
            const user = await this.fetchUserProfile();
            if (user) {
                this.updateUI(user);
                return;
            }
        }
        
        // If not logged in, fetch guest usage
        const usage = await this.fetchGuestUsage();
        this.updateUI(null, usage);
    },

    showModal() {
        const modal = document.getElementById("auth-modal");
        if (modal) {
            // Reset errors
            document.querySelectorAll(".auth-error-alert").forEach(el => el.style.display = "none");
            modal.classList.add("visible");
        }
    },

    hideModal() {
        const modal = document.getElementById("auth-modal");
        if (modal) modal.classList.remove("visible");
    },

    updateUI(user, guestUsage = null) {
        const profileContainer = document.getElementById("sidebar-profile");
        const guestCounter = document.getElementById("guest-counter");
        if (!profileContainer) return;

        if (user) {
            // Logged in: show profile info
            const nameInitial = user.name ? user.name.charAt(0).toUpperCase() : (user.email ? user.email.charAt(0).toUpperCase() : "U");
            const isTest = user.email.toLowerCase() === "test";
            const testBadge = isTest ? '<span style="background: var(--accent-red); color: #fff; font-size: 0.65rem; padding: 2px 6px; border-radius: 4px; font-weight: 700; width: fit-content; margin-top: 2px;">حساب خاص ⚖️</span>' : `<span class="profile-email">${escapeHtml(user.email)}</span>`;
            
            profileContainer.innerHTML = `
                <div class="profile-info">
                    <div class="profile-avatar">${nameInitial}</div>
                    <div class="profile-details">
                        <span class="profile-name">${escapeHtml(user.name || "مستخدم قنوني")}</span>
                        ${testBadge}
                    </div>
                </div>
                <button class="logout-btn" id="logout-btn" title="تسجيل الخروج">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/></svg>
                </button>
            `;
            
            const logoutBtn = document.getElementById("logout-btn");
            if (logoutBtn) {
                logoutBtn.onclick = () => {
                    this.clearSession();
                    window.location.reload();
                };
            }

            if (guestCounter) {
                guestCounter.style.display = "none";
            }
        } else {
            // Guest/Anonymous: show Login/Signup action trigger
            profileContainer.innerHTML = `
                <button class="auth-trigger-btn" id="auth-trigger-btn">تسجيل الدخول / حساب جديد</button>
            `;
            
            const authTriggerBtn = document.getElementById("auth-trigger-btn");
            if (authTriggerBtn) {
                authTriggerBtn.onclick = () => this.showModal();
            }

            // Update guest messages remaining counter
            if (guestCounter) {
                const remaining = guestUsage ? guestUsage.remaining : 5;
                guestCounter.innerText = `باقي ${remaining} استشارات مجانية`;
                guestCounter.style.display = "block";
            }
        }
    },

    async handleAuthSubmit(type) {
        const form = document.getElementById(`${type}-form`);
        const email = form.querySelector('input[name="email"]').value.trim();
        const password = form.querySelector('input[type="password"]').value.trim();
        const nameInput = form.querySelector('input[type="text"]');
        const name = nameInput ? nameInput.value.trim() : "";
        const errorAlert = form.querySelector(".auth-error-alert");
        
        errorAlert.style.display = "none";
        
        const guestId = this.getGuestId();

        try {
            const url = type === "signup" ? "/api/auth/signup" : "/api/auth/login";
            const payload = { email, password, guest_id: guestId };
            if (type === "signup") payload.name = name;

            const res = await fetch(url, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });

            const data = await res.json();
            if (res.ok) {
                this.saveSession(data.token, data.user);
                this.hideModal();
                
                // Clear form inputs
                form.querySelectorAll("input").forEach(i => i.value = "");
                
                // Show a brief success message
                if (data.migrated_conversations > 0) {
                    alert(`تم ربط ${data.migrated_conversations} من محادثاتك السابقة بحسابك الجديد!`);
                }

                // Reload sidebar & conversations list
                this.updateUI(data.user);
                if (window.Sidebar) {
                    await Sidebar.loadConversations();
                }
            } else {
                errorAlert.innerText = data.error || "حدث خطأ غير متوقع.";
                errorAlert.style.display = "block";
            }
        } catch (e) {
            console.error(`[Auth] ${type} error:`, e);
            errorAlert.innerText = "فشل الاتصال بالخادم.";
            errorAlert.style.display = "block";
        }
    }
};

// Helper: Escape HTML string to prevent XSS
function escapeHtml(text) {
    if (!text) return "";
    return text
        .toString()
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}
