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
let isWaiting = false;

function safeDecodeURI(str) {
    if (!str) return "";
    try {
        // Fix missing percent signs in sequences like 'D8%AA' or 'D9%88' at word boundaries
        let normalized = str.replace(/(?<!%)\b([0-9A-Fa-f]{2})%([0-9A-Fa-f]{2})/g, "%$1%$2");
        
        let prev;
        do {
            prev = normalized;
            // Remove spaces and hyphens between percent/hex pairs if they are part of a continuous encoding
            normalized = normalized.replace(/(%[0-9A-Fa-f]{2})\s*-\s*(%[0-9A-Fa-f]{2})/g, "$1$2");
            normalized = normalized.replace(/(%[0-9A-Fa-f]{2})\s+(%[0-9A-Fa-f]{2})/g, "$1$2");
            
            // Handle spaces inside a single code like '% D 8' or '% A 7'
            normalized = normalized.replace(/%\s*([0-9A-Fa-f])\s*([0-9A-Fa-f])/g, "%$1$2");
        } while (normalized !== prev);

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

function cleanTextGarbage(text) {
    if (!text) return "";
    
    let cleaned = text;
    
    // 1. Remove URLs (even with spaces like 'h t t p : / /')
    cleaned = cleaned.replace(/https?:\/\/\S+/gi, "");
    cleaned = cleaned.replace(/h\s*t\s*t\s*p\s*s?:\/\/\S+/gi, "");
    cleaned = cleaned.replace(/www\.\S+/gi, "");
    
    // 2. Remove file names ending in .html or .pdf or similar
    cleaned = cleaned.replace(/\S+\.(html|pdf|txt|doc|docx|aspx|php)\S*/gi, "");
    
    // 3. Remove words containing percent sign (garbage encodings), preserving valid percentage values (like 50% or %50 or ٥٠% or %٥٠)
    cleaned = cleaned.replace(/\S*%+\S*/g, (match) => {
        if (/^\d+%$/g.test(match) || /^%\d+$/g.test(match) || /^[٠-٩]+%$/g.test(match) || /^%[٠-٩]+$/g.test(match)) {
            return match;
        }
        return "";
    });
    
    // 4. Remove leftover random hyphens/spaces at boundaries
    cleaned = cleaned.replace(/^\s*[-–—\s]+\s*/gm, "");
    cleaned = cleaned.replace(/\s*[-–—\s]+\s*$/gm, "");
    
    // 5. Replace multiple spaces with a single space
    cleaned = cleaned.replace(/\s+/g, " ").trim();
    
    return cleaned;
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
    
    // Load legal glossary
    try {
        await loadGlossary();
    } catch (e) {
        console.error("Failed to load glossary during startup", e);
    }

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

    // ── Document Ingestion and Upload Event Bindings ──
    const fileUploadBtn = document.getElementById("file-upload-btn");
    const fileUploadInput = document.getElementById("file-upload-input");

    if (fileUploadBtn && fileUploadInput) {
        fileUploadBtn.onclick = () => {
            fileUploadInput.click();
        };

        fileUploadInput.onchange = async () => {
            if (fileUploadInput.files.length === 0) return;
            const file = fileUploadInput.files[0];
            
            // Render user upload message
            appendUserMsg(`📎 جاري تحميل وتحليل ملف: ${file.name}`);
            
            // Show loading indicator
            showThinking();
            
            const formData = new FormData();
            formData.append("file", file);
            
            try {
                const res = await fetch("/api/documents/upload", {
                    method: "POST",
                    headers: Sidebar.getHeaders(), // Send JWT/Auth headers
                    body: formData
                });
                
                // Remove thinking
                const thinkingRow = document.getElementById("thinking-row");
                if (thinkingRow) thinkingRow.remove();
                
                if (res.ok) {
                    const data = await res.json();
                    appendAiMsg(
                        `📄 **تم الانتهاء من تحليل المستند القانوني:** \`${data.filename}\`\n\nإليك الوقائع والتحليلات الجنائية والمدنية المكتشفة:`,
                        data.retrieved_laws || [],
                        true
                    );
                } else {
                    const err = await res.json();
                    appendAiMsg(`❌ فشل تحليل المستند: ${err.error || "خطأ غير معروف"}`, []);
                }
            } catch (e) {
                console.error(e);
                const thinkingRow = document.getElementById("thinking-row");
                if (thinkingRow) thinkingRow.remove();
                appendAiMsg("❌ عذراً، حصل خطأ أثناء الاتصال بالخادم لتحميل المستند.", []);
            }
            
            // Clear input for future selections
            fileUploadInput.value = "";
        };
    }

    // ── Drafting Assistant Event Bindings ──
    const draftDocBtn = document.getElementById("draft-doc-btn");
    const draftModal = document.getElementById("draft-modal");
    const draftCloseBtn = document.getElementById("draft-close-btn");
    const draftForm = document.getElementById("draft-form");

    if (draftDocBtn && draftModal) {
        draftDocBtn.onclick = () => {
            draftModal.classList.add("visible");
        };
    }
    if (draftCloseBtn && draftModal) {
        draftCloseBtn.onclick = () => {
            draftModal.classList.remove("visible");
        };
    }

    if (draftForm && draftModal) {
        draftForm.onsubmit = async (e) => {
            e.preventDefault();
            
            const templateType = document.getElementById("draft-template-type").value;
            const caseDetails = document.getElementById("draft-case-details").value.trim();
            const customInstructions = document.getElementById("draft-custom-instructions").value.trim();
            
            draftModal.classList.remove("visible");
            
            // Render user request message
            let typeLabel = "عقد قانوني";
            if (templateType === "memo") typeLabel = "مذكرة دفاع";
            else if (templateType === "petition") typeLabel = "صحيفة دعوى";
            
            appendUserMsg(`📝 طلب صياغة ${typeLabel} للموقف التالي:\n${caseDetails}`);
            
            // Show loading indicator
            showThinking();
            
            try {
                const res = await fetch("/api/draft", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        ...Sidebar.getHeaders()
                    },
                    body: JSON.stringify({
                        template_type: templateType,
                        case_details: caseDetails,
                        custom_instructions: customInstructions
                    })
                });
                
                // Remove thinking
                const thinkingRow = document.getElementById("thinking-row");
                if (thinkingRow) thinkingRow.remove();
                
                if (res.ok) {
                    const data = await res.json();
                    appendAiMsg(data.draft, [], true);
                } else {
                    const err = await res.json();
                    appendAiMsg(`❌ فشل الصياغة القانونية: ${err.error || "خطأ غير معروف"}`, []);
                }
            } catch (e) {
                console.error(e);
                const thinkingRow = document.getElementById("thinking-row");
                if (thinkingRow) thinkingRow.remove();
                appendAiMsg("❌ عذراً، حصل خطأ أثناء الاتصال بالخادم لبدء الصياغة.", []);
            }
            
            // Clear textarea inputs
            document.getElementById("draft-case-details").value = "";
            document.getElementById("draft-custom-instructions").value = "";
        };
    }

    // ── Voice Input (Microphone) Binding ──
    const micBtn = document.getElementById("mic-btn");
    if (micBtn && ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window)) {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        const recognition = new SpeechRecognition();
        recognition.lang = 'ar-EG';
        recognition.interimResults = true;
        recognition.continuous = false;

        let isRecording = false;

        micBtn.onclick = () => {
            if (isRecording) {
                recognition.stop();
                return;
            }
            isRecording = true;
            micBtn.classList.add('mic-active');
            micBtn.title = 'جاري الاستماع... اضغط لإيقاف';
            recognition.start();
        };

        recognition.onresult = (event) => {
            let transcript = '';
            for (let i = event.resultIndex; i < event.results.length; i++) {
                transcript += event.results[i][0].transcript;
            }
            if (DOM.input) {
                DOM.input.value = transcript;
                autoResize();
                updateSendBtn();
            }
        };

        recognition.onend = () => {
            isRecording = false;
            micBtn.classList.remove('mic-active');
            micBtn.title = 'إدخال صوتي باللغة العربية';
        };

        recognition.onerror = (event) => {
            console.warn('[Voice] Recognition error:', event.error);
            isRecording = false;
            micBtn.classList.remove('mic-active');
            micBtn.title = 'إدخال صوتي باللغة العربية';
            if (event.error === 'not-allowed') {
                showToast('يرجى السماح بصلاحية الميكروفون في المتصفح.', true);
            }
        };
    } else if (micBtn) {
        micBtn.style.display = 'none';
    }

    // ── Citation Viewer Click Delegation ──
    document.body.addEventListener('click', (e) => {
        const citationEl = e.target.closest('.citation-link');
        if (citationEl) {
            e.preventDefault();
            const citationModal = document.getElementById('citation-modal');
            if (!citationModal) return;

            const lawName = citationEl.dataset.lawName || 'مادة قانونية';
            const articleNum = citationEl.dataset.articleNum || '';
            const snippet = citationEl.dataset.snippet || citationEl.textContent;
            const fullText = citationEl.dataset.fullText || snippet;
            const sourceUrl = citationEl.dataset.sourceUrl || '#';

            document.getElementById('citation-modal-title').innerText = articleNum ? `المادة ${articleNum} — ${lawName}` : lawName;
            
            // Highlight the cited snippet within the full text
            let displayText = escapeHtml(fullText);
            if (snippet && fullText.includes(snippet)) {
                displayText = displayText.replace(
                    escapeHtml(snippet),
                    `<mark style="background-color: rgba(37,99,235,0.2); color: #fff; padding: 2px 4px; border-radius: 3px;">${escapeHtml(snippet)}</mark>`
                );
            }
            document.getElementById('citation-modal-content').innerHTML = displayText;

            document.getElementById('citation-modal-meta').innerText = lawName;
            const linkEl = document.getElementById('citation-modal-link');
            if (sourceUrl && sourceUrl !== '#') {
                linkEl.href = sourceUrl;
                linkEl.style.display = 'flex';
            } else {
                linkEl.style.display = 'none';
            }

            citationModal.classList.add('visible');
        }
    });

    // Citation modal close
    const citationCloseBtn = document.getElementById('citation-close-btn');
    const citationModal = document.getElementById('citation-modal');
    if (citationCloseBtn && citationModal) {
        citationCloseBtn.onclick = () => citationModal.classList.remove('visible');
    }

    newChat();
    if (DOM.input) DOM.input.focus();

    // Glossary Tooltip Popover Event Delegation
    const tooltip = document.getElementById("glossary-tooltip");
    if (tooltip) {
        document.body.addEventListener("mouseover", (e) => {
            const termSpan = e.target.closest(".glossary-term");
            if (termSpan) {
                const term = termSpan.dataset.term;
                const definition = glossaryDict[term];
                if (definition) {
                    tooltip.innerHTML = `<strong>${term}:</strong> ${definition}`;
                    tooltip.style.display = "block";
                    
                    const rect = termSpan.getBoundingClientRect();
                    const tooltipRect = tooltip.getBoundingClientRect();
                    
                    let left = rect.left + window.scrollX + (rect.width / 2) - (tooltipRect.width / 2);
                    left = Math.max(10, left);
                    
                    let top = rect.top + window.scrollY - tooltipRect.height - 8;
                    if (rect.top - tooltipRect.height < 10) {
                        top = rect.bottom + window.scrollY + 8;
                    }
                    
                    tooltip.style.left = `${left}px`;
                    tooltip.style.top = `${top}px`;
                    tooltip.classList.add("visible");
                }
            }
        });
        
        document.body.addEventListener("mouseout", (e) => {
            if (e.target.closest(".glossary-term")) {
                tooltip.classList.remove("visible");
                tooltip.style.display = "none";
            }
        });
    }
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
    try {
        updateTrialStepper();
    } catch (e) {}
}

function renderMessagesFromServer(messages) {
    const rows = DOM.chatMessages.querySelectorAll(".message-row");
    rows.forEach(row => row.remove());
    
    DOM.welcomeScreen.style.display = "none";
    DOM.debateSetup.classList.remove("visible");
    
    let previousQuestion = "";
    messages.forEach(m => {
        if (m.role === "user") {
            appendUserMsg(m.content, false);
            previousQuestion = m.content || "";
        } else {
            appendAiMsg(m.content, m.sources || [], false, previousQuestion);
        }
    });
    scrollToBottom();
    try {
        updateTrialStepper();
    } catch (e) {}
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
    let responseSources = [];

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
                responseSources = Array.isArray(sources) ? sources : [];
                ensureAiBubble();
                renderSources(sources);
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

                textCollector = prepareLegalAnswer(textCollector, responseSources, text);
                const answerNode = aiContentWrapper?.querySelector(".msg-content");
                if (answerNode) answerNode.innerHTML = renderMarkdown(textCollector);
                
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
        renderLegalReferences(aiContentWrapper, sources);
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
            
            if (data.scorecard) {
                appendAiMsg(data.answer, data.sources || []);
            } else {
                appendAiMsg(data.answer, data.sources || []);
            }
            
            try {
                updateTrialStepper();
            } catch (e) {}
            
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

function isLegalQuestionForDisplay(question) {
    return /قانون|قانوني|المادة|مادة|محكمة|قضية|عقد|جنائي|مدني|حقوق|دعوى|تعويض|إيجار|طلاق|نفقة|عقوبة|تشريع|law|legal|article|court|case|contract|rights/i.test(question || "");
}

function verifiedLegalArticles(sources) {
    return (sources || []).map(source => {
        const citation = String(source.citation || "");
        const extracted = citation.match(/(?:المادة|مادة)\s*([\d٠-٩]+)/i);
        return { ...source, display_article_number: source.article_number || (extracted && extracted[1]) || "" };
    }).filter(source =>
        source.display_article_number && ["valid", "amended"].includes(String(source.legal_status || "").toLowerCase())
    );
}

function prepareLegalAnswer(answer, sources, question) {
    const cleanAnswer = String(answer || "").replace(/\[LEGAL STATUS:[^\]]*\]\s*/gi, "").trim();
    if (!isLegalQuestionForDisplay(question) || verifiedLegalArticles(sources).length > 0) return cleanAnswer;
    return "لم أتمكن من التحقق من مادة قانونية منطبقة على هذه المسألة؛ لذلك لا أورد رقم مادة غير موثّق.";
}

function renderLegalReferences(wrapper, sources) {
    const articles = verifiedLegalArticles(sources);
    if (!wrapper || articles.length === 0) return;

    const section = document.createElement("div");
    section.className = "sources-container legal-references";
    const heading = document.createElement("div");
    heading.className = "sources-label";
    heading.textContent = "المراجع القانونية";
    section.appendChild(heading);

    articles.forEach(source => {
        const item = document.createElement("div");
        item.className = "legal-reference-item";
        const article = cleanTextGarbage(safeDecodeURI(source.display_article_number));
        const law = cleanTextGarbage(safeDecodeURI(source.law_name || ""));
        const label = document.createElement("span");
        label.textContent = `المادة ${article}${law ? ` — ${law}` : ""}`;
        item.appendChild(label);

        const verification = source.legal_verification || {};
        const url = verification.source_urls?.[0] || source.source_url || source.url ||
            (/^https?:\/\//i.test(source.citation || "") ? source.citation : "");
        if (url && /^https?:\/\//i.test(url)) {
            const link = document.createElement("a");
            link.href = url;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.textContent = "المصدر";
            item.appendChild(link);
        }
        section.appendChild(item);
    });
    wrapper.appendChild(section);
}

function appendAiMsg(text, sources = [], save = true, question = "") {
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
    const finalText = question ? prepareLegalAnswer(text, sources, question) : String(text || "").replace(/\[LEGAL STATUS:[^\]]*\]\s*/gi, "");
    contentDiv.innerHTML = renderMarkdown(finalText);
    contentWrapper.appendChild(contentDiv);
    renderLegalReferences(contentWrapper, sources);

    addActionsRow(contentWrapper, finalText);

    row.appendChild(avatarDiv);
    row.appendChild(contentWrapper);
    DOM.chatMessages.appendChild(row);
    scrollToBottom();
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
        <button class="msg-action-btn export-timeline-btn" title="مخطط التواريخ والتقادم">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
        </button>
        <button class="msg-action-btn export-docx-btn" title="تصدير كملف Word (.docx)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>
        </button>
        <button class="msg-action-btn export-pdf-btn" title="طباعة وحفظ كـ PDF">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 6 2 18 2 18 9"></polyline><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path><rect x="6" y="14" width="12" height="8"></rect></svg>
        </button>
        <button class="msg-action-btn regenerate-btn" title="إعادة المحاولة">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
        </button>
        <button class="msg-action-btn simplify-btn" title="اشرحها بالعربي البسيط">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"></path><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>
        </button>
    `;

    actionsRow.querySelector(".copy-btn").onclick = () => copyToClipboard(text);
    actionsRow.querySelector(".read-btn").onclick = () => speakText(text);
    
    const timelineBtn = actionsRow.querySelector(".export-timeline-btn");
    if (timelineBtn) {
        timelineBtn.onclick = () => extractAndRenderTimeline(wrapper, text);
    }
    
    const docxBtn = actionsRow.querySelector(".export-docx-btn");
    if (docxBtn) {
        docxBtn.onclick = () => exportToDocx("مستند قانوني Qanuni.AI", text);
    }
    
    const pdfBtn = actionsRow.querySelector(".export-pdf-btn");
    if (pdfBtn) {
        pdfBtn.onclick = () => printToPdf("مستند قانوني Qanuni.AI", text);
    }

    actionsRow.querySelector(".regenerate-btn").onclick = () => {
        const lastUserMsg = getLastUserMessageText();
        if (lastUserMsg) {
            DOM.input.value = lastUserMsg;
            sendMessage();
        }
    };

    const simplifyBtn = actionsRow.querySelector(".simplify-btn");
    if (simplifyBtn) {
        simplifyBtn.onclick = () => explainSimple(text);
    }

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
    
    try {
        html = highlightGlossary(html);
    } catch (e) {
        console.error("Glossary highlight error:", e);
    }
    
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

// ── Export and Formatting Helpers ──

async function exportToDocx(title, content) {
    try {
        showToast("جاري إعداد ملف Word...");
        const res = await fetch("/api/export/docx", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...Sidebar.getHeaders()
            },
            body: JSON.stringify({ title, content })
        });
        if (res.ok) {
            const blob = await res.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `${title}.docx`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            window.URL.revokeObjectURL(url);
            showToast("تم تحميل ملف Word بنجاح!");
        } else {
            const err = await res.json();
            showToast("خطأ: " + (err.error || "فشل تصدير الملف"), true);
        }
    } catch (e) {
        console.error(e);
        showToast("فشل الاتصال بالخادم لتصدير الملف.", true);
    }
}

function printToPdf(title, content) {
    const printSection = document.getElementById("print-section");
    if (!printSection) return;
    
    // Render the markdown content to HTML inside the print section
    printSection.innerHTML = `
        <h1>Qanuni.AI — ${escapeHtml(title)}</h1>
        <div style="direction: rtl; text-align: right;">
            ${renderMarkdown(content)}
        </div>
    `;
    printSection.style.display = "block";
    window.print();
    printSection.style.display = "none";
    printSection.innerHTML = "";
}

function showToast(message, isError = false) {
    const toast = document.createElement("div");
    toast.style.position = "fixed";
    toast.style.bottom = "24px";
    toast.style.left = "50%";
    toast.style.transform = "translateX(-50%)";
    toast.style.backgroundColor = isError ? "#ef4444" : "#10a37f";
    toast.style.color = "#fff";
    toast.style.padding = "8px 16px";
    toast.style.borderRadius = "6px";
    toast.style.fontSize = "0.85rem";
    toast.style.zIndex = "9999";
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 2500);
}

// ── Part 2 Helper Functions ───────────────────────────────────────────

async function loadGlossary() {
    try {
        const res = await fetch("/api/glossary");
        if (res.ok) {
            glossaryDict = await res.json();
        }
    } catch (e) {
        console.error("Failed to load glossary:", e);
    }
}

function highlightGlossary(html) {
    if (Object.keys(glossaryDict).length === 0) return html;
    
    const tempDiv = document.createElement("div");
    tempDiv.innerHTML = html;
    
    function walk(node) {
        if (node.nodeType === Node.TEXT_NODE) {
            let text = node.nodeValue;
            let replaced = false;
            
            for (const term in glossaryDict) {
                const regex = new RegExp(`(?<![أ-ي])${term}(?![أ-ي])`, 'g');
                if (regex.test(text)) {
                    text = text.replace(regex, `<span class="glossary-term" data-term="${term}">${term}</span>`);
                    replaced = true;
                }
            }
            
            if (replaced) {
                const span = document.createElement("span");
                span.innerHTML = text;
                node.parentNode.replaceChild(span, node);
            }
        } else {
            for (let i = node.childNodes.length - 1; i >= 0; i--) {
                walk(node.childNodes[i]);
            }
        }
    }
    
    walk(tempDiv);
    return tempDiv.innerHTML;
}

function updateTrialStepper() {
    const messages = DOM.chatMessages.querySelectorAll(".message-row");
    const msgCount = Array.from(messages).filter(m => m.classList.contains("user-row") || m.classList.contains("ai-row")).length;
    
    document.querySelectorAll(".step-badge").forEach(badge => {
        badge.classList.remove("active");
        badge.style.background = "var(--bg-input)";
        badge.style.color = "var(--text-secondary)";
        badge.style.opacity = "0.5";
    });
    
    let activeStepId = "step-badge-1";
    if (msgCount <= 1) {
        activeStepId = "step-badge-1";
    } else if (msgCount === 2) {
        activeStepId = "step-badge-2";
    } else if (msgCount <= 4) {
        activeStepId = "step-badge-3";
    } else {
        activeStepId = "step-badge-4";
    }
    
    const activeBadge = document.getElementById(activeStepId);
    if (activeBadge) {
        activeBadge.classList.add("active");
        activeBadge.style.background = "var(--accent-blue)";
        activeBadge.style.color = "#fff";
        activeBadge.style.opacity = "1";
    }
}

async function extractAndRenderTimeline(wrapper, text) {
    let container = wrapper.querySelector(".timeline-analysis-container");
    if (container) {
        container.remove();
        return;
    }
    
    showToast("جاري استخراج التواريخ والتقادم القانوني...");
    
    try {
        const res = await fetch("/api/timeline/extract", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...Sidebar.getHeaders()
            },
            body: JSON.stringify({ text })
        });
        
        if (res.ok) {
            const data = await res.json();
            
            container = document.createElement("div");
            container.className = "timeline-analysis-container";
            container.style.marginTop = "14px";
            container.style.border = "1px solid var(--border-color)";
            container.style.borderRadius = "var(--radius-md)";
            container.style.overflow = "hidden";
            container.style.direction = "rtl";
            container.style.textAlign = "right";
            
            const header = document.createElement("div");
            header.style.backgroundColor = "rgba(96, 165, 250, 0.1)";
            header.style.color = "#60a5fa";
            header.style.fontWeight = "700";
            header.style.fontSize = "0.85rem";
            header.style.padding = "8px 12px";
            header.style.borderBottom = "1px solid var(--border-color)";
            header.textContent = "⏱️ مخطط التواريخ ومدد التقادم القانوني";
            container.appendChild(header);
            
            const body = document.createElement("div");
            body.style.padding = "12px";
            body.style.backgroundColor = "rgba(255, 255, 255, 0.01)";
            
            if (data.warnings && data.warnings.length > 0) {
                const warningsTitle = document.createElement("div");
                warningsTitle.style.fontSize = "0.8rem";
                warningsTitle.style.fontWeight = "700";
                warningsTitle.style.marginBottom = "6px";
                warningsTitle.style.color = "#fbbf24";
                warningsTitle.textContent = "⚠️ تنبيهات التقادم والسقوط:";
                body.appendChild(warningsTitle);
                
                data.warnings.forEach(w => {
                    const alertBox = document.createElement("div");
                    alertBox.style.backgroundColor = "rgba(251, 191, 36, 0.06)";
                    alertBox.style.border = "1px dashed rgba(251, 191, 36, 0.3)";
                    alertBox.style.borderRadius = "var(--radius-sm)";
                    alertBox.style.padding = "8px 10px";
                    alertBox.style.marginBottom = "10px";
                    alertBox.style.fontSize = "0.78rem";
                    
                    alertBox.innerHTML = `
                        <div style="font-weight:700; color:#fbbf24;">${escapeHtml(w.type)} (${escapeHtml(w.days_remaining_or_passed)})</div>
                        <div style="color:var(--text-secondary); margin-top:2px; line-height:1.4;">${escapeHtml(w.description)}</div>
                    `;
                    body.appendChild(alertBox);
                });
            }
            
            if (data.events && data.events.length > 0) {
                const timelineTitle = document.createElement("div");
                timelineTitle.style.fontSize = "0.8rem";
                timelineTitle.style.fontWeight = "700";
                timelineTitle.style.marginTop = "12px";
                timelineTitle.style.marginBottom = "10px";
                timelineTitle.style.color = "var(--text-primary)";
                timelineTitle.textContent = "📅 تسلسل وقائع القضية:";
                body.appendChild(timelineTitle);
                
                const timelineGrid = document.createElement("div");
                timelineGrid.style.position = "relative";
                timelineGrid.style.paddingRight = "16px";
                timelineGrid.style.borderRight = "2.5px solid var(--border-color)";
                timelineGrid.style.marginRight = "6px";
                timelineGrid.style.display = "flex";
                timelineGrid.style.flexDirection = "column";
                timelineGrid.style.gap = "14px";
                
                data.events.forEach(ev => {
                    const node = document.createElement("div");
                    node.style.position = "relative";
                    node.style.fontSize = "0.78rem";
                    
                    node.innerHTML = `
                        <span style="position: absolute; right: -21.5px; top: 4px; width: 8px; height: 8px; border-radius: 50%; background-color: #60a5fa; border: 2px solid var(--bg-card);"></span>
                        <div style="font-weight: 700; color: #60a5fa;">${escapeHtml(ev.date)}</div>
                        <div style="font-weight: 600; margin-top: 2px; color: var(--text-primary);">${escapeHtml(ev.event)}</div>
                        <div style="color: var(--text-secondary); font-size: 0.72rem; margin-top: 2px; line-height: 1.3;">الأثر: ${escapeHtml(ev.implication)}</div>
                    `;
                    timelineGrid.appendChild(node);
                });
                body.appendChild(timelineGrid);
            } else {
                const noDates = document.createElement("div");
                noDates.style.fontSize = "0.78rem";
                noDates.style.color = "var(--text-secondary)";
                noDates.textContent = "لم يتم الكشف عن تواريخ محددة في هذا النص.";
                body.appendChild(noDates);
            }
            
            container.appendChild(body);
            
            const actionsRow = wrapper.querySelector(".msg-actions");
            if (actionsRow) {
                wrapper.insertBefore(container, actionsRow);
            } else {
                wrapper.appendChild(container);
            }
            
            showToast("تم تحليل وعرض الجدول الزمني!");
        } else {
            const err = await res.json();
            showToast("خطأ: " + (err.error || "فشل استخراج التواريخ"), true);
        }
    } catch (e) {
        console.error(e);
        showToast("فشل الاتصال بالخادم لتحليل التواريخ.", true);
    }
}

function renderScorecard(wrapper, scorecard) {
    if (!scorecard) return;
    
    const container = document.createElement("div");
    container.className = "scorecard-container";
    container.style.marginTop = "14px";
    container.style.border = "1px solid rgba(139, 92, 246, 0.25)";
    container.style.borderRadius = "var(--radius-md)";
    container.style.overflow = "hidden";
    container.style.direction = "rtl";
    container.style.textAlign = "right";
    
    const header = document.createElement("div");
    header.style.background = "linear-gradient(135deg, rgba(139, 92, 246, 0.2) 0%, rgba(99, 102, 241, 0.2) 100%)";
    header.style.color = "#c084fc";
    header.style.fontWeight = "800";
    header.style.fontSize = "0.88rem";
    header.style.padding = "10px 14px";
    header.style.display = "flex";
    header.style.justifyContent = "space-between";
    header.style.alignItems = "center";
    header.style.borderBottom = "1px solid rgba(139, 92, 246, 0.25)";
    
    header.innerHTML = `
        <span>🏆 تقييم الأداء القانوني للمرافعة</span>
        <span style="background: rgba(168, 85, 247, 0.2); border: 1px solid rgba(168, 85, 247, 0.3); padding: 2px 10px; border-radius: 12px; font-size: 0.8rem;">الدرجة: ${scorecard.score}/100</span>
    `;
    container.appendChild(header);
    
    const body = document.createElement("div");
    body.style.padding = "14px";
    body.style.backgroundColor = "rgba(255, 255, 255, 0.01)";
    body.style.display = "flex";
    body.style.flexDirection = "column";
    body.style.gap = "12px";
    
    let scoreLabel = "مقبول";
    let scoreColor = "#f59e0b";
    if (scorecard.score >= 90) {
        scoreLabel = "ممتاز جداً - مرافعة بليغة وثغرات مدروسة";
        scoreColor = "#10a37f";
    } else if (scorecard.score >= 80) {
        scoreLabel = "جيد جداً - مرافعة قوية وأدلة متناسقة";
        scoreColor = "#3b82f6";
    } else if (scorecard.score >= 65) {
        scoreLabel = "مقبول - مرافعة أساسية بحاجة لأحكام نقض إضافية";
        scoreColor = "#fbbf24";
    } else {
        scoreLabel = "ضعيف - الدفوع تفتقر للربط بنصوص ومواد القانون";
        scoreColor = "#ef4444";
    }
    
    const overview = document.createElement("div");
    overview.style.fontSize = "0.8rem";
    overview.innerHTML = `
        <div style="font-weight: 700; color: ${scoreColor}; font-size: 0.84rem;">التقدير: ${scoreLabel}</div>
    `;
    body.appendChild(overview);
    
    if (scorecard.strengths && scorecard.strengths.length > 0) {
        const strengthsBlock = document.createElement("div");
        strengthsBlock.style.fontSize = "0.8rem";
        strengthsBlock.innerHTML = `
            <strong style="color: #22c55e; display: block; margin-bottom: 4px;">✔ نقاط القوة:</strong>
            <ul style="padding-right: 18px; margin: 0; line-height: 1.4; color: var(--text-secondary);">
                ${scorecard.strengths.map(s => `<li>${escapeHtml(s)}</li>`).join("")}
            </ul>
        `;
        body.appendChild(strengthsBlock);
    }
    
    if (scorecard.weaknesses && scorecard.weaknesses.length > 0) {
        const weaknessesBlock = document.createElement("div");
        weaknessesBlock.style.fontSize = "0.8rem";
        weaknessesBlock.innerHTML = `
            <strong style="color: #ef4444; display: block; margin-bottom: 4px;">❌ نقاط الضعف / القصور:</strong>
            <ul style="padding-right: 18px; margin: 0; line-height: 1.4; color: var(--text-secondary);">
                ${scorecard.weaknesses.map(w => `<li>${escapeHtml(w)}</li>`).join("")}
            </ul>
        `;
        body.appendChild(weaknessesBlock);
    }
    
    if (scorecard.advice) {
        const adviceBlock = document.createElement("div");
        adviceBlock.style.fontSize = "0.8rem";
        adviceBlock.innerHTML = `
            <strong style="color: #60a5fa; display: block; margin-bottom: 4px;">💡 نصيحة للتطوير:</strong>
            <p style="margin: 0; line-height: 1.4; color: var(--text-secondary); text-align: justify;">${escapeHtml(scorecard.advice)}</p>
        `;
        body.appendChild(adviceBlock);
    }
    
    container.appendChild(body);
    
    const actions = wrapper.querySelector(".msg-actions");
    if (actions) {
        wrapper.insertBefore(container, actions);
    } else {
        wrapper.appendChild(container);
    }
}

// ── New AI Feature Renderers ─────────────────────────────────────────

/**
 * Renders a visual reasoning pipeline indicator within a chat bubble.
 * Shows classification → retrieval → reasoning → decision stages.
 */
function renderPipelineStages(wrapper, stageData) {
    let existing = wrapper.querySelector(".pipeline-container");
    if (existing) existing.remove();

    const stages = [
        { key: "classification", label: "التصنيف" },
        { key: "retrieval", label: "الاسترجاع" },
        { key: "reasoning", label: "المداولة" },
        { key: "decision", label: "القرار" }
    ];

    // stageData can be: { stage: "retrieval", status: "active" } or full array
    let activeIdx = -1;
    if (typeof stageData === "object" && stageData.stage) {
        activeIdx = stages.findIndex(s => s.key === stageData.stage);
    }

    const container = document.createElement("div");
    container.className = "pipeline-container";
    container.style.cssText = "position: relative; display: flex; justify-content: space-between; align-items: flex-start; margin: 12px 0; padding: 10px 16px; background: rgba(25,25,25,0.6); border: 1px solid var(--border-color); border-radius: var(--radius-sm);";

    // Background line
    const lineDiv = document.createElement("div");
    lineDiv.className = "pipeline-line";
    lineDiv.innerHTML = `<div class="pipeline-line-progress" style="width: ${activeIdx >= 0 ? ((activeIdx / (stages.length - 1)) * 100) : 0}%"></div>`;
    container.appendChild(lineDiv);

    stages.forEach((s, i) => {
        const step = document.createElement("div");
        let stepClass = "pipeline-step";
        if (i < activeIdx) stepClass += " completed";
        else if (i === activeIdx) stepClass += " active";
        step.className = stepClass;
        step.innerHTML = `
            <div class="pipeline-circle">${i + 1}</div>
            <div class="pipeline-label">${s.label}</div>
        `;
        container.appendChild(step);
    });

    const pActions = wrapper.querySelector(".msg-actions");
    if (pActions) {
        wrapper.insertBefore(container, pActions);
    } else {
        wrapper.appendChild(container);
    }
}

/**
 * Renders an extracted Named Entities panel (people, orgs, courts, dates, money, laws).
 */
function renderEntitiesPanel(wrapper, entities) {
    if (!entities || Object.keys(entities).length === 0) return;
    
    let existing = wrapper.querySelector(".entities-panel");
    if (existing) existing.remove();

    const container = document.createElement("div");
    container.className = "entities-panel";
    container.style.cssText = "margin: 12px 0; padding: 12px; background: rgba(25,25,25,0.6); border: 1px solid var(--border-color); border-radius: var(--radius-sm); direction: rtl; text-align: right;";

    const header = document.createElement("div");
    header.style.cssText = "font-size: 0.82rem; font-weight: 700; color: #60a5fa; margin-bottom: 8px;";
    header.textContent = "🏷️ الكيانات المستخلصة من القضية";
    container.appendChild(header);

    const typeLabels = {
        people: { label: "أشخاص", icon: "👤", color: "#3b82f6" },
        organizations: { label: "مؤسسات/شركات", icon: "🏢", color: "#8b5cf6" },
        courts: { label: "محاكم", icon: "⚖️", color: "#f59e0b" },
        dates: { label: "تواريخ", icon: "📅", color: "#10b981" },
        money: { label: "مبالغ مالية", icon: "💰", color: "#ef4444" },
        laws: { label: "قوانين/مواد", icon: "📜", color: "#06b6d4" }
    };

    const grid = document.createElement("div");
    grid.style.cssText = "display: flex; flex-wrap: wrap; gap: 8px;";

    for (const [type, items] of Object.entries(entities)) {
        if (!items || items.length === 0) continue;
        const meta = typeLabels[type] || { label: type, icon: "📌", color: "#94a3b8" };

        items.forEach(item => {
            const chip = document.createElement("span");
            chip.style.cssText = `padding: 4px 10px; border-radius: 12px; font-size: 0.72rem; font-weight: 600; background: ${meta.color}15; border: 1px solid ${meta.color}30; color: ${meta.color}; display: inline-flex; align-items: center; gap: 4px;`;
            const itemText = typeof item === 'string' ? item : (item.text || item.name || JSON.stringify(item));
            chip.innerHTML = `<span>${meta.icon}</span><span>${escapeHtml(itemText)}</span>`;
            grid.appendChild(chip);
        });
    }

    container.appendChild(grid);

    const eActions = wrapper.querySelector(".msg-actions");
    if (eActions) {
        wrapper.insertBefore(container, eActions);
    } else {
        wrapper.appendChild(container);
    }
}

/**
 * Renders a success probability ring/meter.
 */
function renderSuccessProbability(wrapper, probData) {
    if (!probData) return;
    
    let existing = wrapper.querySelector(".success-prob-container");
    if (existing) existing.remove();

    const prob = typeof probData === "number" ? probData : (probData.probability || probData.score || 50);
    const pct = Math.round(prob <= 1 ? prob * 100 : prob);
    const reasoning = typeof probData === "object" ? (probData.reasoning || "") : "";

    let color = "#ef4444";
    let label = "ضعيف";
    if (pct >= 70) { color = "#2ecc71"; label = "مرتفع"; }
    else if (pct >= 40) { color = "#e67e22"; label = "متوسط"; }

    const container = document.createElement("div");
    container.className = "success-prob-container";
    container.style.cssText = "margin: 12px 0; padding: 14px; background: rgba(25,25,25,0.6); border: 1px solid var(--border-color); border-radius: var(--radius-sm); direction: rtl; text-align: right; display: flex; align-items: center; gap: 16px;";

    container.innerHTML = `
        <div style="position: relative; width: 60px; height: 60px; flex-shrink: 0;">
            <svg viewBox="0 0 36 36" style="width: 60px; height: 60px; transform: rotate(-90deg);">
                <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke="#3c3c3c" stroke-width="3"></path>
                <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke="${color}" stroke-width="3" stroke-dasharray="${pct}, 100" stroke-linecap="round"></path>
            </svg>
            <div style="position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font-size: 0.85rem; font-weight: 800; color: ${color};">${pct}%</div>
        </div>
        <div style="display: flex; flex-direction: column; gap: 4px; flex: 1;">
            <div style="font-size: 0.85rem; font-weight: 700; color: #fff;">احتمالية النجاح المقدرة: <span style="color: ${color};">${label}</span></div>
            ${reasoning ? `<div style="font-size: 0.76rem; color: var(--text-secondary); line-height: 1.4;">${escapeHtml(reasoning)}</div>` : ""}
        </div>
    `;

    const sActions = wrapper.querySelector(".msg-actions");
    if (sActions) {
        wrapper.insertBefore(container, sActions);
    } else {
        wrapper.appendChild(container);
    }
}

/**
 * Renders an automatically generated case timeline within a chat bubble.
 */
function renderAutoTimeline(wrapper, timelineData) {
    if (!timelineData || timelineData.length === 0) return;

    let existing = wrapper.querySelector(".auto-timeline-container");
    if (existing) existing.remove();

    const container = document.createElement("div");
    container.className = "auto-timeline-container";
    container.style.cssText = "margin: 12px 0; padding: 14px; background: rgba(25,25,25,0.6); border: 1px solid var(--border-color); border-radius: var(--radius-sm); direction: rtl; text-align: right;";

    const header = document.createElement("div");
    header.style.cssText = "font-size: 0.82rem; font-weight: 700; color: #60a5fa; margin-bottom: 10px;";
    header.textContent = "📅 الجدول الزمني للقضية (تلقائي)";
    container.appendChild(header);

    const list = document.createElement("div");
    list.style.cssText = "display: flex; flex-direction: column; gap: 8px; padding-right: 16px; border-right: 2px solid var(--border-color); margin-right: 6px;";

    timelineData.forEach(evt => {
        const item = document.createElement("div");
        item.style.cssText = "position: relative;";
        
        const date = evt.date || evt.event_date || "";
        const title = evt.event || evt.title || "";
        const desc = evt.implication || evt.description || "";
        const isDeadline = evt.is_deadline;

        item.innerHTML = `
            <span style="position: absolute; right: -22px; top: 3px; width: 10px; height: 10px; border-radius: 50%; background: ${isDeadline ? '#e74c3c' : '#60a5fa'}; border: 2px solid var(--bg-main);"></span>
            <div style="font-weight: 700; font-size: 0.76rem; color: ${isDeadline ? '#e74c3c' : '#60a5fa'};">${escapeHtml(date)} ${isDeadline ? '⚠️' : ''}</div>
            <div style="font-weight: 600; font-size: 0.8rem; color: #fff;">${escapeHtml(title)}</div>
            ${desc ? `<div style="font-size: 0.72rem; color: var(--text-secondary); line-height: 1.3; margin-top: 2px;">${escapeHtml(desc)}</div>` : ""}
        `;
        list.appendChild(item);
    });

    container.appendChild(list);

    const tActions = wrapper.querySelector(".msg-actions");
    if (tActions) {
        wrapper.insertBefore(container, tActions);
    } else {
        wrapper.appendChild(container);
    }
}

/**
 * Renders clickable follow-up suggestion chips.
 */
function renderFollowUpChips(wrapper, followUps) {
    if (!followUps || followUps.length === 0) return;

    let existing = wrapper.querySelector(".follow-up-container");
    if (existing) existing.remove();

    const container = document.createElement("div");
    container.className = "follow-up-container";

    const label = document.createElement("div");
    label.style.cssText = "font-size: 0.72rem; font-weight: 600; color: var(--text-muted); margin-bottom: 4px; width: 100%;";
    label.textContent = "💡 أسئلة متابعة مقترحة:";
    container.appendChild(label);

    followUps.forEach(q => {
        const chip = document.createElement("button");
        chip.className = "follow-up-chip";
        chip.textContent = typeof q === "string" ? q : (q.question || q.text || JSON.stringify(q));
        chip.onclick = () => {
            if (DOM.input) {
                DOM.input.value = chip.textContent;
                autoResize();
                updateSendBtn();
                sendMessage();
            }
        };
        container.appendChild(chip);
    });

    const fActions = wrapper.querySelector(".msg-actions");
    if (fActions) {
        wrapper.insertBefore(container, fActions);
    } else {
        wrapper.appendChild(container);
    }
}

/**
 * Simplify legal text into plain Arabic via the /api/explain-simple endpoint.
 */
async function explainSimple(text) {
    showToast("جاري تبسيط النص القانوني...");
    try {
        const res = await fetch("/api/explain-simple", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...Sidebar.getHeaders()
            },
            body: JSON.stringify({ text: text })
        });

        if (res.ok) {
            const data = await res.json();
            appendAiMsg(`🗣️ **شرح مبسط:**\n\n${data.simplified || data.result || "لم يتم التبسيط."}`, []);
        } else {
            showToast("فشل تبسيط النص القانوني.", true);
        }
    } catch (e) {
        console.error(e);
        showToast("خطأ في الاتصال بالخادم.", true);
    }
}
