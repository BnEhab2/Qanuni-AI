/**
 * Qanuni.AI — Main Application & Chat Coordinator (v3.0)
 * ======================================================
 * Manages core conversational cycles, suggestions, mode selectors (Search vs. Debate),
 * text-to-speech, copy, scroll management, and DOM binding.
 */

// Global App State
let currentConvId = null;
let currentMode = "research";
let debateSessionId = null;
let debateCaseDescription = "";
let userRole = "defense";
let webSearchEnabled = false;
let currentAbortController = null;

function safeDecodeURI(str) {
    if (!str) return "";
    try {
        // Fix missing percent signs in sequences like 'D8%AA' or 'D9%88' at word boundaries
        let normalized = str.replace(/\b([0-9A-Fa-f]{2})%([0-9A-Fa-f]{2})/g, "%$1%$2");
        
        // Remove spaces and hyphens between percent/hex pairs if they are part of a continuous encoding
        normalized = normalized.replace(/(%[0-9A-Fa-f]{2})\s*-\s*(%[0-9A-Fa-f]{2})/g, "$1$2");
        normalized = normalized.replace(/(%[0-9A-Fa-f]{2})\s+(%[0-9A-Fa-f]{2})/g, "$1$2");
        
        // Handle spaces inside a single code like '% D 8' or '% A 7'
        normalized = normalized.replace(/%\s*([0-9A-Fa-f])\s*([0-9A-Fa-f])/g, "%$1$2");

        // Decode contiguous percent-encoded sequences
        return normalized.replace(/(%[0-9A-Fa-f]{2})+/g, (match) => {
            try {
                return decodeURIComponent(match);
            } catch (e) {
                // If the entire block fails, try to decode the longest possible valid sub-sequences
                let decoded = "";
                let i = 0;
                while (i < match.length) {
                    let success = false;
                    for (let len = match.length - i; len >= 3; len -= 3) {
                        let sub = match.substr(i, len);
                        try {
                            decoded += decodeURIComponent(sub);
                            i += len;
                            success = true;
                            break;
                        } catch (err) {}
                    }
                    if (!success) {
                        decoded += match.substr(i, 3);
                        i += 3;
                    }
                }
                return decoded;
            }
        });
    } catch (e) {
        // Fallback on total failure
    }
    return str;
}


const DOM = {
    chatContainer:   document.getElementById("chat-container"),
    chatMessages:    document.getElementById("chat-messages"),
    welcomeScreen:   document.getElementById("welcome-screen"),
    input:           document.getElementById("user-input"),
    sendBtn:         document.getElementById("send-btn"),
    suggestions:     document.getElementById("suggestions"),
    scrollDownBtn:   document.getElementById("scroll-down-btn"),
    debateBanner:    document.getElementById("debate-banner"),
    debateSetup:     document.getElementById("debate-setup"),
    debateCaseInput: document.getElementById("debate-case-input"),
    debateStartBtn:  document.getElementById("debate-start-btn"),
    debateRoles:     document.getElementById("debate-roles"),
    modeResearch:    document.getElementById("mode-research"),
    modeDebate:      document.getElementById("mode-debate"),
    newChatTopBtn:   document.getElementById("new-chat-top-btn"),
    newChatBtn:      document.getElementById("new-chat-btn"),
};

// ── Application Initialization ────────────────────────────────────────
window.addEventListener("DOMContentLoaded", async () => {
    // 1. Initialize Auth
    await Auth.init();

    // 2. Initialize Sidebar & Search
    await Sidebar.loadConversations();
    Sidebar.initSearch();
    Sidebar.initMobile();

    // 3. Bind UI click handlers
    if (DOM.newChatTopBtn) DOM.newChatTopBtn.onclick = newChat;
    if (DOM.newChatBtn) DOM.newChatBtn.onclick = newChat;

    const webSearchToggle = document.getElementById("web-search-toggle");
    if (webSearchToggle) {
        webSearchToggle.onclick = () => {
            webSearchEnabled = !webSearchEnabled;
            webSearchToggle.classList.toggle("active", webSearchEnabled);
            webSearchToggle.title = webSearchEnabled ? "البحث على الإنترنت مفعّل" : "تفعيل البحث على الإنترنت";
        };
    }

    if (DOM.modeResearch) DOM.modeResearch.onclick = () => switchMode("research");
    if (DOM.modeDebate) DOM.modeDebate.onclick = () => switchMode("debate");

    if (DOM.debateCaseInput) {
        DOM.debateCaseInput.oninput = () => {
            DOM.debateStartBtn.disabled = DOM.debateCaseInput.value.trim().length === 0;
        };
        DOM.debateCaseInput.onkeydown = e => {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                const val = DOM.debateCaseInput.value.trim();
                if (val.length > 0) {
                    DOM.debateStartBtn.click();
                }
            }
        };
    }

    if (DOM.debateStartBtn) {
        DOM.debateStartBtn.onclick = async () => {
            debateCaseDescription = DOM.debateCaseInput.value.trim();
            if (!debateCaseDescription) return;

            // Trigger guest count check on client-side
            if (!Auth.isLoggedIn()) {
                const guestUsage = await Auth.fetchGuestUsage();
                if (guestUsage && guestUsage.remaining === 0) {
                    Auth.showModal();
                    return;
                }
            }

            DOM.debateSetup.classList.remove("visible");
            DOM.welcomeScreen.style.display = "none";
            
            // Create debate session
            try {
                const res = await fetch("/api/conversations", {
                    method: "POST",
                    headers: Sidebar.getHeaders(),
                    body: JSON.stringify({ mode: "debate" })
                });
                if (res.ok) {
                    const data = await res.json();
                    currentConvId = data.conversation_id;
                    debateSessionId = data.conversation_id;
                    await Sidebar.loadConversations();
                }
            } catch (e) {
                console.error(e);
            }

            const openingMsg = "القضية: " + debateCaseDescription;
            appendUserMsg(openingMsg);
            sendDebateMessage(debateCaseDescription, debateCaseDescription);
        };
    }

    // Role Segment controls inside debate banner & setup
    document.querySelectorAll(".role-segment-btn").forEach(btn => {
        btn.onclick = () => updateUserRole(btn.dataset.role);
    });

    if (DOM.debateRoles) {
        DOM.debateRoles.querySelectorAll(".debate-role-card").forEach(card => {
            card.onclick = () => updateUserRole(card.dataset.role);
        });
    }

    // Bind scroll events
    if (DOM.scrollDownBtn) DOM.scrollDownBtn.onclick = scrollToBottom;
    if (DOM.chatContainer) DOM.chatContainer.onscroll = handleScroll;

    // Bind input box actions
    if (DOM.input) {
        DOM.input.oninput = () => { autoResize(); updateSendBtn(); };
        DOM.input.onkeydown = e => {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (!isWaiting) sendMessage();
            }
        };
        DOM.input.onfocus = () => {
            setTimeout(scrollToBottom, 300);
        };
    }

    if (DOM.sendBtn) {
        DOM.sendBtn.onclick = () => {
            if (isWaiting) {
                if (currentAbortController) {
                    currentAbortController.abort();
                }
            } else {
                sendMessage();
            }
        };
    }

    // Bind initial specialties
    document.querySelectorAll(".specialty-card").forEach(card => {
        card.onclick = () => {
            document.querySelectorAll(".specialty-card").forEach(c => c.classList.remove("active"));
            card.classList.add("active");
            loadSuggestions(card.dataset.spec);
        };
    });

    // Bind debate initial suggestions
    document.querySelectorAll(".debate-suggestion-card").forEach(card => {
        card.onclick = () => {
            if (DOM.debateCaseInput) {
                DOM.debateCaseInput.value = card.dataset.case;
                DOM.debateCaseInput.dispatchEvent(new Event("input"));
            }
        };
    });

    newChat();
    if (DOM.input) DOM.input.focus();
});

// ── Application Flow Controls ────────────────────────────────────────

function newChat() {
    currentConvId = null;
    debateSessionId = null;
    debateCaseDescription = "";
    clearChat();
}

function clearChat() {
    const rows = DOM.chatMessages.querySelectorAll(".message-row");
    rows.forEach(row => row.remove());
    
    if (!DOM.chatMessages.contains(DOM.welcomeScreen)) {
        DOM.chatMessages.appendChild(DOM.welcomeScreen);
    }
    if (!DOM.chatMessages.contains(DOM.debateSetup)) {
        DOM.chatMessages.appendChild(DOM.debateSetup);
    }
    
    if (currentMode === "debate") {
        DOM.welcomeScreen.style.display = "none";
        DOM.debateSetup.classList.add("visible");
        if (DOM.debateCaseInput) {
            DOM.debateCaseInput.value = "";
            DOM.debateStartBtn.disabled = true;
        }
    } else {
        DOM.welcomeScreen.style.display = "flex";
        DOM.debateSetup.classList.remove("visible");
        
        document.querySelectorAll(".specialty-card").forEach(c => c.classList.remove("active"));
        const generalCard = document.querySelector('.specialty-card[data-spec="general"]');
        if (generalCard) generalCard.classList.add("active");
        loadSuggestions("general");
    }
    if (DOM.scrollDownBtn) DOM.scrollDownBtn.style.display = "none";
}

function renderMessagesFromServer(messages) {
    const rows = DOM.chatMessages.querySelectorAll(".message-row");
    rows.forEach(row => row.remove());
    
    DOM.welcomeScreen.style.display = "none";
    DOM.debateSetup.classList.remove("visible");
    
    messages.forEach(m => {
        if (m.role === "user") {
            appendUserMsg(m.content, false);
        } else {
            appendAiMsg(
                m.content, 
                m.sources || [], 
                false, 
                null, 
                [], 
                [], 
                m.prosecution || null, 
                m.defense || null, 
                m.judge_decision || null
            );
        }
    });
    scrollToBottom();
}

function switchMode(mode, triggerNewChat = true) {
    currentMode = mode;
    if (DOM.modeResearch) DOM.modeResearch.classList.toggle("active", mode === "research");
    if (DOM.modeDebate) DOM.modeDebate.classList.toggle("active", mode === "debate");
    if (DOM.debateBanner) DOM.debateBanner.classList.toggle("visible", mode === "debate");

    if (mode === "debate") {
        if (triggerNewChat) newChat();
        DOM.welcomeScreen.style.display = "none";
        DOM.debateSetup.classList.add("visible");
        updateUserRole("defense");
    } else {
        DOM.input.placeholder = "اسأل عن أي شيء قانوني...";
        DOM.debateSetup.classList.remove("visible");
        if (triggerNewChat) newChat();
    }
}

function updateUserRole(role) {
    userRole = role;
    
    document.querySelectorAll(".role-segment-btn").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.role === role);
    });

    if (DOM.debateRoles) {
        DOM.debateRoles.querySelectorAll(".debate-role-card").forEach(c => {
            c.classList.toggle("selected", c.dataset.role === role);
        });
    }

    if (currentMode === "debate") {
        DOM.input.placeholder = (role === "prosecution") ? "اكتب حجة النيابة بتاعتك..." : "اكتب حجة الدفاع بتاعتك...";
    } else {
        DOM.input.placeholder = "اسأل عن أي شيء قانوني...";
    }
}

// ── Message Senders ──────────────────────────────────────────────────

async function sendMessage() {
    const text = DOM.input.value.trim();
    if (!text || isWaiting) return;

    // Check guest count client-side first
    if (!Auth.isLoggedIn()) {
        const guestUsage = await Auth.fetchGuestUsage();
        if (guestUsage && guestUsage.remaining === 0) {
            Auth.showModal();
            return;
        }
    }

    if (currentMode === "debate") {
        if (!debateSessionId) {
            debateCaseDescription = text;
            debateSessionId = "debate_" + Date.now();
        }
        if (!currentConvId) {
            try {
                const res = await fetch("/api/conversations", {
                    method: "POST",
                    headers: Sidebar.getHeaders(),
                    body: JSON.stringify({ mode: "debate" })
                });
                if (res.ok) {
                    const data = await res.json();
                    currentConvId = data.conversation_id;
                    debateSessionId = data.conversation_id;
                }
            } catch (e) {
                console.error(e);
            }
        }
        appendUserMsg(text);
        DOM.input.value = "";
        autoResize();
        sendDebateMessage(text, debateCaseDescription);
        return;
    }

    // Auto-create research chat conversation if first message
    if (!currentConvId) {
        try {
            const res = await fetch("/api/conversations", {
                method: "POST",
                headers: Sidebar.getHeaders(),
                body: JSON.stringify({ mode: "research" })
            });
            if (res.ok) {
                const data = await res.json();
                currentConvId = data.conversation_id;
                // Add active state to items
                await Sidebar.loadConversations();
            }
        } catch (e) {
            console.error(e);
        }
    }

    appendUserMsg(text);
    DOM.input.value = "";
    autoResize();
    isWaiting = true;
    updateSendBtn();

    showThinking();
    currentAbortController = new AbortController();

    let aiMsgElement = null;
    let aiContentWrapper = null;
    let textCollector = "";

    const payload = {
        message: text,
        web_search: webSearchEnabled,
        conversation_id: currentConvId,
        session_id: currentConvId
    };

    const headers = Sidebar.getHeaders();
    
    // Inject abort signal
    payload.signal = currentAbortController.signal;

    await Streaming.stream(
        "/api/chat/stream",
        payload,
        headers,
        {
            onSources(sources) {
                ensureAiBubble();
                renderSources(sources);
            },
            onClassification(category) {
                console.log("[Pipeline] Classified:", category);
            },
            onConfidence(confidence) {
                ensureAiBubble();
                renderConfidence(aiContentWrapper, confidence);
            },
            onContradictions(contradictions) {
                ensureAiBubble();
                renderContradictions(aiContentWrapper, contradictions);
            },
            onRisks(risks) {
                ensureAiBubble();
                renderRisks(aiContentWrapper, risks);
            },
            onProsecution(prosecution) {
                ensureAiBubble();
                aiContentWrapper.prosecutionData = prosecution;
                renderAdvocacy(aiContentWrapper, prosecution, aiContentWrapper.defenseData || null);
            },
            onDefense(defense) {
                ensureAiBubble();
                aiContentWrapper.defenseData = defense;
                renderAdvocacy(aiContentWrapper, aiContentWrapper.prosecutionData || null, defense);
            },
            onJudgeDecision(judgeDecision) {
                ensureAiBubble();
                renderJudgeDecision(aiContentWrapper, judgeDecision);
            },
            onToken(token) {
                removeThinking();
                ensureAiBubble();
                textCollector += token;
                aiContentWrapper.querySelector(".msg-content").innerHTML = renderMarkdown(textCollector);
                scrollToBottom();
            },
            onDone(event) {
                isWaiting = false;
                updateSendBtn();
                
                // Add message actions to bubble
                addActionsRow(aiContentWrapper, textCollector);
                
                // Reload conversations list in sidebar (picks up new auto-titles)
                Sidebar.loadConversations();
                
                // Update guest usage badges
                if (event.remaining_messages !== undefined) {
                    if (event.remaining_messages === 0 && !Auth.isLoggedIn()) {
                        Auth.showModal();
                    }
                    Auth.fetchGuestUsage().then(usage => Auth.updateUI(null, usage));
                }
            },
            onError(errorMsg) {
                removeThinking();
                appendAiMsg("خطأ: " + errorMsg, []);
                isWaiting = false;
                updateSendBtn();
            },
            onLimitReached() {
                removeThinking();
                isWaiting = false;
                updateSendBtn();
                Auth.showModal();
            }
        }
    );

    function ensureAiBubble() {
        if (aiMsgElement) return;
        
        DOM.welcomeScreen.style.display = "none";
        DOM.debateSetup.classList.remove("visible");
        
        aiMsgElement = document.createElement("div");
        aiMsgElement.className = "message-row ai-row";

        const avatarDiv = document.createElement("div");
        avatarDiv.className = "ai-avatar";
        avatarDiv.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 16l3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1zM2 16l3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1zM7 21h10M12 2v19M20 7H4"/></svg>`;

        aiContentWrapper = document.createElement("div");
        aiContentWrapper.className = "msg-content-wrapper";

        const contentDiv = document.createElement("div");
        contentDiv.className = "msg-content";
        aiContentWrapper.appendChild(contentDiv);

        aiMsgElement.appendChild(avatarDiv);
        aiMsgElement.appendChild(aiContentWrapper);
        DOM.chatMessages.appendChild(aiMsgElement);
        scrollToBottom();
    }

    function renderSources(sources) {
        if (!sources || sources.length === 0) return;

        const srcContainer = document.createElement("div");
        srcContainer.className = "sources-container";
        srcContainer.innerHTML = '<div class="sources-label">المصادر القانونية</div>';
        
        sources.forEach(s => {
            const card = document.createElement("div");
            card.className = "source-card";
            const isWebSource = s.source === "web_search" || s.retrieval_method === "web_search";
            const webBadge = isWebSource ? '<span class="source-web-badge">🌐 إنترنت</span>' : '';
            card.innerHTML = `
                <div class="source-header">
                    <span class="source-name">${escapeHtml(safeDecodeURI(s.law_name))}</span>
                    ${webBadge}
                    <span class="source-cat">${escapeHtml(safeDecodeURI(s.categories || ""))}</span>
                    <svg class="source-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 9l6 6 6-6"/></svg>
                </div>
                <div class="source-body">
                    <div class="source-text">${escapeHtml(safeDecodeURI(s.snippet))}</div>
                </div>
            `;
            card.querySelector(".source-header").onclick = () => card.classList.toggle("open");
            srcContainer.appendChild(card);
        });
        
        aiContentWrapper.appendChild(srcContainer);
    }
}

async function sendDebateMessage(messageText, caseDesc) {
    if (isWaiting) return;
    isWaiting = true;
    updateSendBtn();
    showThinking();
    currentAbortController = new AbortController();

    try {
        const res = await fetch("/api/debate", {
            method: "POST",
            headers: Sidebar.getHeaders(),
            body: JSON.stringify({
                message: messageText,
                case_description: caseDesc,
                session_id: debateSessionId,
                user_role: userRole,
                conversation_id: currentConvId
            }),
            signal: currentAbortController.signal
        });
        
        if (res.status === 403) {
            removeThinking();
            Auth.showModal();
            return;
        }

        const data = await res.json();
        removeThinking();

        if (data.error) {
            appendAiMsg("خطأ: " + data.error, []);
        } else {
            if (data.session_id) debateSessionId = data.session_id;
            appendAiMsg(data.answer, data.sources || []);
            
            // Reload conversations list in sidebar
            Sidebar.loadConversations();
            
            if (data.remaining_messages !== undefined) {
                if (data.remaining_messages === 0 && !Auth.isLoggedIn()) {
                    Auth.showModal();
                }
                Auth.fetchGuestUsage().then(usage => Auth.updateUI(null, usage));
            }
        }
    } catch (err) {
        removeThinking();
        if (err.name === "AbortError") {
            appendAiMsg("تم إيقاف إنشاء الرد.", []);
        } else {
            appendAiMsg("عذراً، حصل خطأ في الاتصال بالخادم.", []);
        }
    } finally {
        isWaiting = false;
        updateSendBtn();
    }
}

// ── UI Drawing Functions ──────────────────────────────────────────────

function appendUserMsg(text, save = true) {
    DOM.welcomeScreen.style.display = "none";
    DOM.debateSetup.classList.remove("visible");
    
    const row = document.createElement("div");
    row.className = "message-row user-row";
    row.innerHTML = `
        <div class="msg-content-wrapper">
            <div class="msg-content">${escapeHtml(text)}</div>
            <div class="msg-actions">
                <button class="msg-action-btn copy-btn" title="نسخ الرسالة">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                </button>
            </div>
        </div>
    `;

    row.querySelector(".copy-btn").onclick = () => copyToClipboard(text);

    DOM.chatMessages.appendChild(row);
    scrollToBottom();
}

function appendAiMsg(text, sources = [], save = true, confidence = null, contradictions = [], risks = [], prosecution = null, defense = null, judgeDecision = null) {
    DOM.welcomeScreen.style.display = "none";
    DOM.debateSetup.classList.remove("visible");

    const row = document.createElement("div");
    row.className = "message-row ai-row";

    const avatarDiv = document.createElement("div");
    avatarDiv.className = "ai-avatar";
    avatarDiv.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 16l3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1zM2 16l3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1zM7 21h10M12 2v19M20 7H4"/></svg>`;

    const contentWrapper = document.createElement("div");
    contentWrapper.className = "msg-content-wrapper";

    const contentDiv = document.createElement("div");
    contentDiv.className = "msg-content";
    contentDiv.innerHTML = renderMarkdown(text);
    contentWrapper.appendChild(contentDiv);

    // Render new elements if provided
    if (confidence) {
        renderConfidence(contentWrapper, confidence);
    }
    if (contradictions && contradictions.length > 0) {
        renderContradictions(contentWrapper, contradictions);
    }
    if (risks && risks.length > 0) {
        renderRisks(contentWrapper, risks);
    }

    // Render Prosecution and Defense if provided
    if (prosecution || defense) {
        contentWrapper.prosecutionData = prosecution;
        contentWrapper.defenseData = defense;
        renderAdvocacy(contentWrapper, prosecution, defense);
    }

    // Render Judge Decision if provided
    if (judgeDecision && judgeDecision.verdict) {
        renderJudgeDecision(contentWrapper, judgeDecision);
    }

    if (sources && sources.length > 0) {
        const srcContainer = document.createElement("div");
        srcContainer.className = "sources-container";
        srcContainer.innerHTML = '<div class="sources-label">المصادر القانونية</div>';
        sources.forEach(s => {
            const card = document.createElement("div");
            card.className = "source-card";
            const isWebSource = s.source === "web_search" || s.retrieval_method === "web_search";
            const webBadge = isWebSource ? '<span class="source-web-badge">🌐 إنترنت</span>' : '';
            card.innerHTML = `
                <div class="source-header">
                    <span class="source-name">${escapeHtml(safeDecodeURI(s.law_name))}</span>
                    ${webBadge}
                    <span class="source-cat">${escapeHtml(safeDecodeURI(s.categories || ""))}</span>
                    <svg class="source-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 9l6 6 6-6"/></svg>
                </div>
                <div class="source-body">
                    <div class="source-text">${escapeHtml(safeDecodeURI(s.snippet))}</div>
                </div>
            `;
            card.querySelector(".source-header").onclick = () => card.classList.toggle("open");
            srcContainer.appendChild(card);
        });
        contentWrapper.appendChild(srcContainer);
    }

    addActionsRow(contentWrapper, text);

    row.appendChild(avatarDiv);
    row.appendChild(contentWrapper);
    DOM.chatMessages.appendChild(row);
    scrollToBottom();
}

function renderConfidence(wrapper, conf) {
    if (!conf || conf.overall_confidence === undefined) return;
    
    let existing = wrapper.querySelector(".confidence-badge");
    if (existing) existing.remove();
    
    const badge = document.createElement("div");
    const pct = Math.round(conf.overall_confidence * 100);
    let badgeClass = "conf-low";
    let dot = "🔴";
    
    if (conf.confidence_label === "high") {
        badgeClass = "conf-high";
        dot = "🟢";
    } else if (conf.confidence_label === "medium") {
        badgeClass = "conf-medium";
        dot = "🟡";
    }
    
    badge.className = `confidence-badge ${badgeClass}`;
    badge.innerHTML = `
        <span class="conf-dot">${dot}</span>
        <span class="conf-text">${escapeHtml(conf.confidence_label_ar)} (${pct}%)</span>
    `;
    
    wrapper.insertBefore(badge, wrapper.firstChild);

    // Render warnings if any
    if (conf.warnings && conf.warnings.length > 0) {
        let warnDiv = wrapper.querySelector(".confidence-warnings");
        if (warnDiv) warnDiv.remove();
        
        warnDiv = document.createElement("div");
        warnDiv.className = "confidence-warnings";
        conf.warnings.forEach(w => {
            const p = document.createElement("div");
            p.className = "warning-item";
            p.textContent = safeDecodeURI(w);
            warnDiv.appendChild(p);
        });
        wrapper.appendChild(warnDiv);
    }
}

function renderContradictions(wrapper, contradictions) {
    if (!contradictions || contradictions.length === 0) return;
    
    let existing = wrapper.querySelector(".contradictions-container");
    if (existing) existing.remove();
    
    const container = document.createElement("div");
    container.className = "contradictions-container";
    container.innerHTML = '<div class="contradictions-label">⚠️ تعارضات قانونية محتملة</div>';
    
    contradictions.forEach(c => {
        const card = document.createElement("div");
        card.className = `contradiction-card severity-${c.severity}`;
        card.innerHTML = `
            <div class="contradiction-header">
                <span class="contradiction-title">${escapeHtml(safeDecodeURI(c.article_a_name))} ↔ ${escapeHtml(safeDecodeURI(c.article_b_name))}</span>
            </div>
            <div class="contradiction-body">
                <div class="contradiction-desc">${escapeHtml(safeDecodeURI(c.description))}</div>
                ${c.resolution_hint ? `<div class="contradiction-hint"><strong>الحل المقترح:</strong> ${escapeHtml(safeDecodeURI(c.resolution_hint))}</div>` : ''}
            </div>
        `;
        container.appendChild(card);
    });
    
    const actions = wrapper.querySelector(".msg-actions");
    if (actions) {
        wrapper.insertBefore(container, actions);
    } else {
        wrapper.appendChild(container);
    }
}

function renderRisks(wrapper, risks) {
    if (!risks || risks.length === 0) return;
    
    let existing = wrapper.querySelector(".risks-container");
    if (existing) existing.remove();
    
    const container = document.createElement("div");
    container.className = "risks-container";
    container.innerHTML = '<div class="risks-label">📊 تقييم المخاطر القانونية</div>';
    
    const grid = document.createElement("div");
    grid.className = "risks-grid";
    
    risks.forEach(r => {
        const card = document.createElement("div");
        card.className = `risk-card risk-level-${r.risk_level}`;
        card.innerHTML = `
            <div class="risk-header">
                <span class="risk-issue">${escapeHtml(safeDecodeURI(r.issue))}</span>
                <span class="risk-badge">${escapeHtml(safeDecodeURI(r.risk_level.toUpperCase()))}</span>
            </div>
            <div class="risk-body">
                <div class="risk-detail"><strong>الشدة:</strong> ${escapeHtml(safeDecodeURI(r.severity))} | <strong>الاحتمالية:</strong> ${escapeHtml(safeDecodeURI(r.probability))}</div>
                <div class="risk-desc">${escapeHtml(safeDecodeURI(r.description))}</div>
            </div>
        `;
        grid.appendChild(card);
    });
    
    container.appendChild(grid);
    
    const actions = wrapper.querySelector(".msg-actions");
    if (actions) {
        wrapper.insertBefore(container, actions);
    } else {
        wrapper.appendChild(container);
    }
}



function addActionsRow(wrapper, text) {
    const actionsRow = document.createElement("div");
    actionsRow.className = "msg-actions";
    actionsRow.innerHTML = `
        <button class="msg-action-btn copy-btn" title="نسخ الإجابة">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
        </button>
        <button class="msg-action-btn thumbs-up-btn" title="مفيد">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"></path></svg>
        </button>
        <button class="msg-action-btn thumbs-down-btn" title="غير مفيد">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h3a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-3"></path></svg>
        </button>
        <button class="msg-action-btn read-btn" title="قراءة بصوت">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"></path></svg>
        </button>
        <button class="msg-action-btn regenerate-btn" title="إعادة المحاولة">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
        </button>
    `;

    actionsRow.querySelector(".copy-btn").onclick = () => copyToClipboard(text);
    actionsRow.querySelector(".read-btn").onclick = () => speakText(text);
    actionsRow.querySelector(".regenerate-btn").onclick = () => {
        const lastUserMsg = getLastUserMessageText();
        if (lastUserMsg) {
            DOM.input.value = lastUserMsg;
            sendMessage();
        }
    };

    wrapper.appendChild(actionsRow);
}

function showThinking() {
    if (document.getElementById("thinking-row")) return;
    
    const row = document.createElement("div");
    row.className = "message-row ai-row";
    row.id = "thinking-row";

    const thinkingMsg = webSearchEnabled
        ? "🌐 جاري البحث على الإنترنت وتحليل النصوص القانونية..."
        : "جاري تحليل الموقف والنصوص القانونية...";
    const thinkingClass = webSearchEnabled ? "thinking-text thinking-web" : "thinking-text";

    row.innerHTML = `
        <div class="ai-avatar">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 16l3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1zM2 16l3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1zM7 21h10M12 2v19M20 7H4"/></svg>
        </div>
        <div class="msg-content-wrapper">
            <div class="thinking-indicator">
                <div class="thinking-pulse"><span></span><span></span><span></span></div>
                <span class="${thinkingClass}">${thinkingMsg}</span>
            </div>
        </div>
    `;
    DOM.chatMessages.appendChild(row);
    scrollToBottom();
}

function removeThinking() {
    const el = document.getElementById("thinking-row");
    if (el) el.remove();
}

// ── Specialty Recommendations & Helper Actions ───────────────────────

const ICONS = {
    lock: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path></svg>`,
    scale: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 16l3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1zM2 16l3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1zM7 21h10M12 2v19M20 7H4"/></svg>`,
    file: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>`,
    warning: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>`
};

const specialtyQuestions = {
    general: [
        { q: "ما هي عقوبة السرقة في القانون المصري؟", icon: ICONS.lock },
        { q: "حقوق الزوجة في قانون الأحوال الشخصية", icon: ICONS.scale },
        { q: "إجراءات رفع دعوى مدنية في مصر", icon: ICONS.file },
        { q: "عقوبة النصب والاحتيال في القانون المصري", icon: ICONS.warning }
    ],
    criminal: [
        { q: "ما هي شروط حالات الدفاع الشرعي عن النفس؟", icon: ICONS.scale },
        { q: "متى تسقط العقوبة في الجنايات والجنح بمضي المدة؟", icon: ICONS.lock },
        { q: "ما هو الفرق بين القتل العمد والقتل الخطأ وعقوبتهما؟", icon: ICONS.warning },
        { q: "ما هي عقوبة التزوير في المحررات الرسمية؟", icon: ICONS.file }
    ],
    commercial: [
        { q: "ما هي التزامات التجار في القيد بالدفاتر التجارية؟", icon: ICONS.file },
        { q: "ما هي عقوبة كتابة شيك بدون رصيد في مصر؟", icon: ICONS.warning },
        { q: "ما هي شروط صحة عقد الوكالة التجارية؟", icon: ICONS.scale },
        { q: "كيف يتم إثبات المعاملات التجارية قانوناً؟", icon: ICONS.lock }
    ],
    corporate: [
        { q: "ما هي خطوات تأسيس شركة مساهمة في مصر؟", icon: ICONS.file },
        { q: "ما الفرق بين شركة الشخص الواحد والشركة ذات المسؤولية المحدودة؟", icon: ICONS.scale },
        { q: "ما هي شروط وإجراءات تصفية الشركات وانقضائها؟", icon: ICONS.warning },
        { q: "ما هي مسؤولية الشركاء والمديرين عن ديون الشركة؟", icon: ICONS.lock }
    ],
    family: [
        { q: "ما هي حقوق الزوجة المطلقة من نفقة ومتعة ومؤخر؟", icon: ICONS.scale },
        { q: "كيف يتم حساب نفقة الصغار والولاية التعليمية؟", icon: ICONS.file },
        { q: "ما هي شروط الخلع وإجراءاته في القانون المصري؟", icon: ICONS.warning },
        { q: "ما هي قواعد تقسيم الميراث والتركات للورثة؟", icon: ICONS.lock }
    ],
    labor: [
        { q: "ما هي حقوق العامل في حالة الفصل التعسفي؟", icon: ICONS.scale },
        { q: "كيف يتم حساب مكافأة نهاية الخدمة ورصيد الإجازات؟", icon: ICONS.file },
        { q: "ما هي المدة القانونية لتقديم شكوى لمكتب العمل؟", icon: ICONS.warning },
        { q: "ما هي شروط إنهاء عقد العمل محدد المدة؟", icon: ICONS.lock }
    ],
    investment: [
        { q: "ما هي الحوافز والضمانات التي يقدمها قانون الاستثمار؟", icon: ICONS.scale },
        { q: "كيف يمكن للمستثمر الأجنبي الحصول على الإقامة الاستثمارية؟", icon: ICONS.file },
        { q: "ما هي إجراءات الحصول على الرخصة الذهبية للمشروعات؟", icon: ICONS.warning },
        { q: "ما هي القيود المفروضة على تحويل أرباح الشركات للخارج؟", icon: ICONS.lock }
    ],
    real_estate: [
        { q: "ما الفرق بين دعوى صحة ونفاذ ودعوى صحة التوقيع؟", icon: ICONS.scale },
        { q: "كيف يتم التعامل القانوني مع مخالفات البناء والتصالح؟", icon: ICONS.warning },
        { q: "ما هي شروط تملك الأجانب للعقارات في مصر؟", icon: ICONS.file },
        { q: "ما هي إجراءات الطرد لعدم دفع الأجرة في القانون الجديد؟", icon: ICONS.lock }
    ],
    international_private: [
        { q: "ما هو القانون واجب التطبيق في منازعات العقود الدولية؟", icon: ICONS.file },
        { q: "كيف يتم تنفيذ الأحكام الأجنبية داخل مصر؟", icon: ICONS.scale },
        { q: "ما هي شروط صحة شرط التحكيم الدولي في العقود؟", icon: ICONS.lock },
        { q: "ما هي قواعد الاختصاص القضائي في المنازعات ذات العنصر الأجنبي؟", icon: ICONS.warning }
    ],
    international_public: [
        { q: "ما هي شروط انضمام مصر للاتفاقيات والمعاهدات الدولية؟", icon: ICONS.file },
        { q: "كيف يتم فض المنازعات الحدودية والاقليمية دولياً؟", icon: ICONS.scale },
        { q: "ما هو دور محكمة العدل الدولية في النزاعات بين الدول؟", icon: ICONS.lock },
        { q: "كيف تطبق الحصانات الدبلوماسية والقنصلية في القانون المصري؟", icon: ICONS.warning }
    ]
};

function loadSuggestions(category) {
    const questions = specialtyQuestions[category];
    if (!questions || !DOM.suggestions) return;
    
    DOM.suggestions.style.opacity = "0";
    setTimeout(() => {
        DOM.suggestions.innerHTML = "";
        questions.forEach(item => {
            const card = document.createElement("div");
            card.className = "suggestion-card";
            card.dataset.q = item.q;
            
            const iconHtml = item.icon ? `<span class="suggestion-icon">${item.icon}</span>` : "";
            
            card.innerHTML = `
                ${iconHtml}
                <span class="suggestion-text">${item.q}</span>
            `;
            
            card.onclick = () => {
                DOM.input.value = card.dataset.q;
                autoResize();
                updateSendBtn();
                sendMessage();
            };
            
            DOM.suggestions.appendChild(card);
        });
        DOM.suggestions.style.opacity = "1";
    }, 150);
}

// ── Sizing, Animation, Copy & Utility Helpers ─────────────────────────

function autoResize() {
    if (!DOM.input) return;
    DOM.input.style.height = "auto";
    DOM.input.style.height = Math.min(DOM.input.scrollHeight, 200) + "px";
}

function updateSendBtn() {
    if (!DOM.sendBtn) return;
    if (isWaiting) {
        DOM.sendBtn.classList.add("stop-mode");
        DOM.sendBtn.classList.remove("active");
        DOM.sendBtn.title = "إيقاف الإنشاء";
        DOM.sendBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="12" height="12"></rect></svg>';
    } else {
        DOM.sendBtn.classList.remove("stop-mode");
        DOM.sendBtn.title = "إرسال الرسالة";
        DOM.sendBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>';
        
        const hasText = DOM.input.value.trim().length > 0;
        DOM.sendBtn.classList.toggle("active", hasText);
    }
}

function handleScroll() {
    if (!DOM.chatContainer || !DOM.scrollDownBtn) return;
    const c = DOM.chatContainer;
    const isNearBottom = c.scrollHeight - c.scrollTop - c.clientHeight < 150;
    DOM.scrollDownBtn.style.display = isNearBottom ? "none" : "flex";
}

function scrollToBottom() {
    if (!DOM.chatContainer) return;
    requestAnimationFrame(() => {
        DOM.chatContainer.scrollTop = DOM.chatContainer.scrollHeight;
    });
}

function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => {
        const toast = document.createElement("div");
        toast.style.position = "fixed";
        toast.style.bottom = "24px";
        toast.style.left = "50%";
        toast.style.transform = "translateX(-50%)";
        toast.style.backgroundColor = "#10a37f";
        toast.style.color = "#fff";
        toast.style.padding = "8px 16px";
        toast.style.borderRadius = "6px";
        toast.style.fontSize = "0.85rem";
        toast.style.zIndex = "9999";
        toast.textContent = "تم النسخ بنجاح";
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 2000);
    });
}

function speakText(text) {
    if ("speechSynthesis" in window) {
        if (window.speechSynthesis.speaking) {
            window.speechSynthesis.cancel();
            return;
        }
        const cleanText = text.replace(/[*#`_\-]/g, ""); 
        const utterance = new SpeechSynthesisUtterance(cleanText);
        utterance.lang = "ar-EG";
        window.speechSynthesis.speak(utterance);
    }
}

function getLastUserMessageText() {
    // Locate the last user bubble text from DOM
    const userBubbles = DOM.chatMessages.querySelectorAll(".user-row .msg-content");
    if (userBubbles.length > 0) {
        return userBubbles[userBubbles.length - 1].innerText;
    }
    return "";
}

function renderMarkdown(text) {
    let html = escapeHtml(text);
    html = html.replace(/^### (.+)$/gm, "<h4>$1</h4>");
    html = html.replace(/^## (.+)$/gm, "<h3>$1</h3>");
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/^[-•] (.+)$/gm, "<li>$1</li>");
    html = html.replace(/(<li>.*<\/li>\n?)+/g, "<ul>$&</ul>");
    html = html.replace(/^\d+\. (.+)$/gm, "<li>$1</li>");
    html = html.replace(/\n\n/g, "</p><p>");
    html = html.replace(/\n/g, "<br>");
    html = "<p>" + html + "</p>";
    html = html.replace(/<p>\s*<\/p>/g, "");
    return html;
}

function renderAdvocacy(wrapper, prosecution, defense) {
    if (!prosecution && !defense) return;
    
    let container = wrapper.querySelector(".advocacy-container");
    if (!container) {
        container = document.createElement("div");
        container.className = "advocacy-container";
        const actions = wrapper.querySelector(".msg-actions");
        if (actions) {
            wrapper.insertBefore(container, actions);
        } else {
            wrapper.appendChild(container);
        }
    }
    
    // Render Prosecution Column
    let prosDiv = container.querySelector(".prosecution-side");
    if (prosecution) {
        if (!prosDiv) {
            prosDiv = document.createElement("div");
            prosDiv.className = "advocacy-side prosecution-side";
            container.appendChild(prosDiv);
        }
        const articlesHtml = (prosecution.supporting_articles || []).map(a => `<li>${escapeHtml(safeDecodeURI(a))}</li>`).join("");
        const riskLabel = prosecution.risk_view === "high" ? "عالي الخطورة" : prosecution.risk_view === "medium" ? "متوسط" : "منخفض";
        prosDiv.innerHTML = `
            <div class="side-header">
                <span class="side-icon">⚖️</span>
                <span class="side-title">مرافعة النيابة العامة (الادعاء)</span>
            </div>
            <div class="side-body">
                <p class="side-arg"><strong>الاتهام الأساسي:</strong> ${escapeHtml(safeDecodeURI(prosecution.argument || ""))}</p>
                <div class="side-reasoning"><strong>منطق النيابة:</strong> ${escapeHtml(safeDecodeURI(prosecution.reasoning || ""))}</div>
                ${articlesHtml ? `<div class="side-articles"><strong>المواد المستند إليها:</strong><ul>${articlesHtml}</ul></div>` : ""}
            </div>
            <div class="side-footer">
                <span class="side-confidence">قوة القضية: ${Math.round((prosecution.confidence || 0) * 100)}%</span>
                <span class="side-risk risk-${prosecution.risk_view || 'medium'}">خطورة الجرم: ${riskLabel}</span>
            </div>
        `;
    }
    
    // Render Defense Column
    let defDiv = container.querySelector(".defense-side");
    if (defense) {
        if (!defDiv) {
            defDiv = document.createElement("div");
            defDiv.className = "advocacy-side defense-side";
            container.appendChild(defDiv);
        }
        const articlesHtml = (defense.supporting_articles || []).map(a => `<li>${escapeHtml(safeDecodeURI(a))}</li>`).join("");
        const riskLabel = defense.risk_view === "high" ? "شديد" : defense.risk_view === "medium" ? "متوسط" : "مخفف";
        defDiv.innerHTML = `
            <div class="side-header">
                <span class="side-icon">🛡️</span>
                <span class="side-title">دفوع محامي الدفاع</span>
            </div>
            <div class="side-body">
                <p class="side-arg"><strong>الحجة الأساسية:</strong> ${escapeHtml(safeDecodeURI(defense.argument || ""))}</p>
                <div class="side-reasoning"><strong>منطق الدفاع:</strong> ${escapeHtml(safeDecodeURI(defense.reasoning || ""))}</div>
                ${articlesHtml ? `<div class="side-articles"><strong>المواد المستند إليها:</strong><ul>${articlesHtml}</ul></div>` : ""}
            </div>
            <div class="side-footer">
                <span class="side-confidence">ثقة الدفاع: ${Math.round((defense.confidence || 0) * 100)}%</span>
                <span class="side-risk risk-${defense.risk_view || 'low'}">مستوى الخطر: ${riskLabel}</span>
            </div>
        `;
    }
}

function renderJudgeDecision(wrapper, decision) {
    if (!decision || !decision.verdict) return;
    
    let existing = wrapper.querySelector(".judge-decision-container");
    if (existing) existing.remove();
    
    const container = document.createElement("div");
    container.className = `judge-decision-container verdict-${decision.verdict}`;
    
    let verdictIcon = "⚖️";
    if (decision.verdict === "guilty") {
        verdictIcon = "🔨";
    } else if (decision.verdict === "not_guilty") {
        verdictIcon = "🕊️";
    }
    
    const factorsHtml = (decision.key_factors || []).map(f => `<li>${escapeHtml(safeDecodeURI(f))}</li>`).join("");
    
    container.innerHTML = `
        <div class="judge-header">
            <span class="judge-icon">${verdictIcon}</span>
            <div class="judge-title-box">
                <span class="judge-title">حكم منصة القضاء</span>
                <span class="judge-verdict-badge verdict-${decision.verdict}">الحكم: ${escapeHtml(safeDecodeURI(decision.verdict_ar || ""))}</span>
            </div>
            <span class="judge-confidence">ثقة المحكمة: ${Math.round((decision.confidence || 0) * 100)}%</span>
        </div>
        <div class="judge-body">
            <div class="judge-reasoning">
                <strong>حيثيات الحكم وأسبابه:</strong>
                <p>${escapeHtml(safeDecodeURI(decision.reasoning || ""))}</p>
            </div>
            ${factorsHtml ? `
            <div class="judge-factors">
                <strong>العوامل الحاسمة في الحكم:</strong>
                <ul>${factorsHtml}</ul>
            </div>
            ` : ""}
        </div>
    `;
    
    const actions = wrapper.querySelector(".msg-actions");
    if (actions) {
        wrapper.insertBefore(container, actions);
    } else {
        wrapper.appendChild(container);
    }
}
