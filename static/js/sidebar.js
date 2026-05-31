/**
 * Qanuni.AI — Sidebar & Conversation History Controller (v3.0)
 * ==========================================================
 * Dynamically loads and filters historical user chats, manages CRUD (Delete),
 * and handles responsive drawer mechanics on mobile devices.
 */

const Sidebar = {
    conversations: [],
    
    // Get headers depending on auth state (JWT or Guest ID)
    getHeaders() {
        const headers = { "Content-Type": "application/json" };
        if (Auth.isLoggedIn()) {
            headers["Authorization"] = `Bearer ${Auth.getToken()}`;
        } else {
            headers["X-Guest-ID"] = Auth.getGuestId();
        }
        return headers;
    },

    async loadConversations() {
        try {
            const res = await fetch("/api/conversations", {
                headers: this.getHeaders()
            });
            if (res.ok) {
                const data = await res.json();
                this.conversations = data.conversations;
                this.renderConversations(this.conversations);
            }
        } catch (e) {
            console.error("[Sidebar] Failed to load conversations:", e);
        }
    },

    renderConversations(list) {
        const container = document.getElementById("chat-history");
        if (!container) return;

        container.innerHTML = "";

        if (list.length === 0) {
            container.innerHTML = `
                <div style="text-align: center; color: var(--text-muted); font-size: 0.8rem; margin-top: 20px; direction: rtl;">
                    لا توجد محادثات سابقة.
                </div>
            `;
            return;
        }

        list.forEach(c => {
            const item = document.createElement("div");
            item.className = "chat-history-item";
            if (c.id === currentConvId) {
                item.classList.add("active");
            }
            
            // Icon according to mode (search vs. debate)
            const iconSvg = c.mode === "debate" 
                ? `<svg class="chat-item-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>`
                : `<svg class="chat-item-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>`;

            item.innerHTML = `
                <div class="chat-item-main">
                    ${iconSvg}
                    <span class="chat-item-title">${escapeHtml(c.title || "محادثة جديدة")}</span>
                </div>
                <button class="chat-item-delete-btn" title="حذف المحادثة">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
                </button>
            `;

            // Click to load conversation
            item.querySelector(".chat-item-main").onclick = () => {
                this.selectConversation(c.id, c.mode);
            };

            // Click to delete conversation
            item.querySelector(".chat-item-delete-btn").onclick = (e) => {
                e.stopPropagation();
                this.deleteConversation(c.id);
            };

            container.appendChild(item);
        });
    },

    async selectConversation(convId, mode) {
        currentConvId = convId;
        
        // Match sidebar mode to target conversation mode
        if (currentMode !== mode) {
            switchMode(mode, false); // Switch mode without initiating a new chat
        }
        
        this.renderConversations(this.conversations);
        this.closeMobileSidebar();
        
        // Load messages from server
        try {
            showThinking();
            const res = await fetch(`/api/conversations/${convId}`, {
                headers: this.getHeaders()
            });
            removeThinking();
            
            if (res.ok) {
                const data = await res.json();
                
                // Set up debate details if conversation is debate mode
                if (mode === "debate") {
                    debateSessionId = data.conversation.id; // Share ID
                    // Locate first user message or case description
                    const userMsgs = data.messages.filter(m => m.role === "user");
                    if (userMsgs.length > 0) {
                        debateCaseDescription = userMsgs[0].content.replace(/^القضية:\s*/, "");
                    }
                }
                
                renderMessagesFromServer(data.messages);
            } else {
                console.error("[Sidebar] Failed to load messages");
            }
        } catch (e) {
            removeThinking();
            console.error("[Sidebar] Error fetching messages:", e);
        }
    },

    async deleteConversation(convId) {
        if (!confirm("هل أنت متأكد من رغبتك في حذف هذه المحادثة؟")) return;

        try {
            const res = await fetch(`/api/conversations/${convId}`, {
                method: "DELETE",
                headers: this.getHeaders()
            });
            if (res.ok) {
                // Remove from local list
                this.conversations = this.conversations.filter(c => c.id !== convId);
                
                // If we deleted the active conversation, start a new one
                if (currentConvId === convId) {
                    newChat();
                } else {
                    this.renderConversations(this.conversations);
                }
            }
        } catch (e) {
            console.error("[Sidebar] Failed to delete conversation:", e);
        }
    },

    initSearch() {
        const searchInput = document.getElementById("chat-search");
        if (!searchInput) return;

        let debounceTimeout;
        searchInput.oninput = () => {
            clearTimeout(debounceTimeout);
            const query = searchInput.value.trim();

            if (query.length < 2) {
                this.renderConversations(this.conversations);
                return;
            }

            debounceTimeout = setTimeout(async () => {
                try {
                    const res = await fetch("/api/conversations/search", {
                        method: "POST",
                        headers: this.getHeaders(),
                        body: JSON.stringify({ query })
                    });
                    if (res.ok) {
                        const data = await res.json();
                        this.renderConversations(data.conversations);
                    }
                } catch (e) {
                    console.error("[Sidebar] Search error:", e);
                }
            }, 300);
        };
    },

    // Mobile specific operations
    initMobile() {
        const toggleBtn = document.getElementById("sidebar-toggle");
        const sidebar = document.getElementById("sidebar");
        
        if (toggleBtn && sidebar) {
            toggleBtn.onclick = (e) => {
                e.stopPropagation();
                sidebar.classList.toggle("open");
            };
        }

        // Close sidebar when clicking outside on mobile devices
        document.addEventListener("click", (e) => {
            if (sidebar && sidebar.classList.contains("open") && !sidebar.contains(e.target) && e.target !== toggleBtn) {
                this.closeMobileSidebar();
            }
        });
    },

    closeMobileSidebar() {
        const sidebar = document.getElementById("sidebar");
        if (sidebar) sidebar.classList.remove("open");
    }
};
