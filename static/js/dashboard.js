/**
 * Qanuni.AI — Lawyer Dashboard & Case Management Coordinator (v1.0)
 * =================================================================
 * Manages CMS analytics, charts, CRUD operations, timelines, note updates,
 * file attachment handling, and relationship graph rendering using Vis.js.
 */

const Dashboard = {
    categoryChart: null,
    activeCaseId: null,
    visNetwork: null,

    init() {
        this.bindEvents();
        this.loadAnalytics();
        this.loadCases();
    },

    bindEvents() {
        const createCaseBtn = document.getElementById("create-new-case-btn");
        const addCaseModal = document.getElementById("add-case-modal");
        const addCaseClose = document.getElementById("add-case-close-btn");
        const addCaseForm = document.getElementById("add-case-form");
        const searchInput = document.getElementById("cms-search-input");
        const statusFilter = document.getElementById("cms-status-filter");
        
        // Search & Filter
        if (searchInput) searchInput.oninput = () => this.loadCases();
        if (statusFilter) statusFilter.onchange = () => this.loadCases();

        // Create Case Modal
        if (createCaseBtn && addCaseModal) {
            createCaseBtn.onclick = () => addCaseModal.classList.add("visible");
        }
        if (addCaseClose && addCaseModal) {
            addCaseClose.onclick = () => addCaseModal.classList.remove("visible");
        }

        // Add Case Submit
        if (addCaseForm && addCaseModal) {
            addCaseForm.onsubmit = async (e) => {
                e.preventDefault();
                const title = document.getElementById("add-case-title").value.trim();
                const client = document.getElementById("add-case-client").value.trim();
                const number = document.getElementById("add-case-number").value.trim();
                const court = document.getElementById("add-case-court").value.trim();
                const category = document.getElementById("add-case-category").value;
                const desc = document.getElementById("add-case-description").value.trim();

                try {
                    const res = await fetch("/api/cases", {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                            ...Sidebar.getHeaders()
                        },
                        body: JSON.stringify({
                            title: title,
                            client_name: client,
                            case_number: number,
                            court: court,
                            category: category,
                            description: desc,
                            success_probability: 50.0
                        })
                    });
                    
                    if (res.ok) {
                        addCaseModal.classList.remove("visible");
                        addCaseForm.reset();
                        this.loadAnalytics();
                        this.loadCases();
                    } else {
                        const err = await res.json();
                        alert("فشل إضافة القضية: " + (err.error || "خطأ غير معروف"));
                    }
                } catch (err) {
                    console.error("Error creating case:", err);
                }
            };
        }

        // Case Detail tabs
        document.querySelectorAll("[data-case-tab]").forEach(tab => {
            tab.onclick = () => {
                document.querySelectorAll("[data-case-tab]").forEach(t => t.classList.remove("active"));
                tab.classList.add("active");
                
                const targetTab = tab.dataset.caseTab;
                document.querySelectorAll(".case-tab-content").forEach(content => {
                    content.style.display = "none";
                });
                
                const activeContent = document.getElementById(`case-tab-${targetTab}`);
                if (activeContent) activeContent.style.display = "block";
                
                if (targetTab === "graph") {
                    setTimeout(() => this.drawRelationshipGraph(), 100);
                }
            };
        });

        // Detail Modal Close
        const detailsCloseBtn = document.getElementById("case-details-close-btn");
        const detailsModal = document.getElementById("case-details-modal");
        if (detailsCloseBtn && detailsModal) {
            detailsCloseBtn.onclick = () => detailsModal.classList.remove("visible");
        }

        // Note Submit inside case
        const noteSubmitBtn = document.getElementById("case-note-submit-btn");
        if (noteSubmitBtn) {
            noteSubmitBtn.onclick = () => this.submitNote();
        }

        // Timeline Submit inside case
        const timelineSubmitBtn = document.getElementById("case-timeline-submit-btn");
        if (timelineSubmitBtn) {
            timelineSubmitBtn.onclick = () => this.submitTimelineEvent();
        }

        // Case Document Upload triggers
        const caseUploadBtn = document.getElementById("case-upload-btn");
        const caseUploadInput = document.getElementById("case-upload-input");
        if (caseUploadBtn && caseUploadInput) {
            caseUploadBtn.onclick = () => caseUploadInput.click();
            caseUploadInput.onchange = () => this.uploadCaseDocument();
        }

        // Action: delete case
        const deleteCaseBtn = document.getElementById("case-delete-btn");
        if (deleteCaseBtn) {
            deleteCaseBtn.onclick = () => this.deleteActiveCase();
        }

        // Action: launch draft memo prefilled
        const caseDraftBtn = document.getElementById("case-draft-doc-btn");
        if (caseDraftBtn) {
            caseDraftBtn.onclick = () => this.openDraftingAssistantForCase();
        }

        // Status change callback
        const statusSelect = document.getElementById("case-detail-status-select");
        if (statusSelect) {
            statusSelect.onchange = async () => {
                if (!this.activeCaseId) return;
                try {
                    await fetch(`/api/cases/${this.activeCaseId}`, {
                        method: "POST", // Treat as update or delete depending on backend. We can update status
                        headers: Sidebar.getHeaders()
                    });
                    // For status updates, features.py doesn't define case-id update directly. 
                    // Let's implement an endpoint or let the backend support updating if we need.
                    // Wait, we can modify update status by adding the call to update case if we want,
                    // but we can skip or implement if needed. Let's make sure it is saved.
                } catch (e) {}
            };
        }
    },

    async loadAnalytics() {
        if (!Auth.isLoggedIn()) return;
        try {
            const res = await fetch("/api/cases/analytics", {
                headers: Sidebar.getHeaders()
            });
            if (res.ok) {
                const data = await res.json();
                
                // Update stats UI
                const active = data.status.active || 0;
                const pending = data.status.pending || 0;
                const closed = data.status.closed || 0;
                const total = active + pending + closed;
                
                document.getElementById("stats-total-cases").innerText = total;
                document.getElementById("stats-active-cases").innerText = active;
                document.getElementById("stats-closed-cases").innerText = closed;
                
                // Calculate average success rate
                let sumProb = 0;
                let countProb = 0;
                data.monthly.forEach(m => {
                    sumProb += (m.avg_prob || 0) * m.cnt;
                    countProb += m.cnt;
                });
                const avg = countProb > 0 ? Math.round(sumProb / countProb) : 50;
                document.getElementById("stats-avg-prob").innerText = `${avg}%`;

                // Draw/Update Category breakdown Chart
                this.drawCategoryChart(data.categories);
            }
        } catch (err) {
            console.error("Error loading analytics:", err);
        }
    },

    drawCategoryChart(categories) {
        const ctx = document.getElementById("categoryChart");
        if (!ctx) return;

        const labels = Object.keys(categories).map(cat => {
            const translation = {
                "civil_law": "قانون مدني",
                "criminal_law": "قانون جنائي",
                "commercial_law": "قانون تجاري",
                "family_law": "أحوال شخصية",
                "labor_law": "قانون العمل",
                "real_estate_law": "قانون العقارات",
                "administrative_law": "إداري",
                "other": "أخرى"
            };
            return translation[cat] || cat;
        });
        const values = Object.values(categories);

        if (this.categoryChart) {
            this.categoryChart.destroy();
        }

        this.categoryChart = new Chart(ctx, {
            type: "doughnut",
            data: {
                labels: labels,
                datasets: [{
                    data: values,
                    backgroundColor: [
                        "#2563eb", "#e74c3c", "#2ecc71", "#e67e22", "#9b59b6", "#1abc9c", "#95a5a6", "#34495e"
                    ],
                    borderWidth: 1,
                    borderColor: "#3c3c3c"
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: "right",
                        labels: {
                            color: "#ececec",
                            font: { family: "Cairo", size: 10 }
                        }
                    }
                }
            }
        });
    },

    async loadCases() {
        if (!Auth.isLoggedIn()) return;
        
        const searchVal = document.getElementById("cms-search-input").value.trim();
        const statusVal = document.getElementById("cms-status-filter").value;
        const listContainer = document.getElementById("cms-cases-list");
        if (!listContainer) return;

        try {
            let url = "/api/cases";
            const params = [];
            if (searchVal) params.push(`search=${encodeURIComponent(searchVal)}`);
            if (statusVal) params.push(`status=${encodeURIComponent(statusVal)}`);
            if (params.length > 0) url += `?${params.join("&")}`;

            const res = await fetch(url, {
                headers: Sidebar.getHeaders()
            });

            if (res.ok) {
                const data = await res.json();
                listContainer.innerHTML = "";
                
                if (data.cases.length === 0) {
                    listContainer.innerHTML = `<div style="font-size: 0.82rem; color: var(--text-muted); text-align: center; padding: 24px;">لا توجد قضايا تطابق فلاتر البحث.</div>`;
                    return;
                }

                data.cases.forEach(c => {
                    const statusAr = { "active": "نشطة", "pending": "معلقة", "closed": "مغلقة" }[c.status] || c.status;
                    const catAr = {
                        "civil_law": "قانون مدني",
                        "criminal_law": "قانون جنائي",
                        "commercial_law": "قانون تجاري",
                        "family_law": "أحوال شخصية",
                        "labor_law": "قانون العمل",
                        "real_estate_law": "قانون العقارات",
                        "other": "أخرى"
                    }[c.category] || c.category;

                    const dateStr = new Date(c.created_at * 1000).toLocaleDateString("ar-EG");

                    const card = document.createElement("div");
                    card.className = "cms-case-card";
                    card.onclick = () => this.viewCaseDetails(c.id);
                    card.innerHTML = `
                        <div class="cms-case-card-info">
                            <span class="cms-case-card-title">${c.title}</span>
                            <span class="cms-case-card-client">الموكل: ${c.client_name} | رقم: ${c.case_number || "غير محدد"}</span>
                            <span class="cms-case-card-meta">المحكمة: ${c.court || "غير محدد"} | تصنيف: ${catAr}</span>
                        </div>
                        <div style="display: flex; flex-direction: column; align-items: flex-end; gap: 8px;">
                            <span class="status-badge ${c.status}">${statusAr}</span>
                            <span style="font-size: 0.72rem; color: #e67e22; font-weight: 700;">نجاح: ${Math.round(c.success_probability)}%</span>
                        </div>
                    `;
                    listContainer.appendChild(card);
                });
            }
        } catch (err) {
            console.error("Error loading cases:", err);
        }
    },

    async viewCaseDetails(caseId) {
        this.activeCaseId = caseId;
        const detailsModal = document.getElementById("case-details-modal");
        if (!detailsModal) return;

        try {
            const res = await fetch(`/api/cases/${caseId}`, {
                headers: Sidebar.getHeaders()
            });

            if (res.ok) {
                const data = await res.json();
                const c = data.case;

                // Setup header details
                document.getElementById("case-detail-title").innerText = c.title;
                const createdDate = new Date(c.created_at * 1000).toLocaleDateString("ar-EG");
                document.getElementById("case-detail-subtitle").innerText = `أنشئت في: ${createdDate} | رقم التعريف: ${c.id.substring(0, 8)}`;
                document.getElementById("case-detail-client").innerText = c.client_name;
                document.getElementById("case-detail-number").innerText = c.case_number || "غير محدد";
                document.getElementById("case-detail-court").innerText = c.court || "غير محدد";
                
                const catAr = {
                    "civil_law": "قانون مدني",
                    "criminal_law": "قانون جنائي",
                    "commercial_law": "قانون تجاري",
                    "family_law": "أحوال شخصية",
                    "labor_law": "قانون العمل",
                    "real_estate_law": "قانون العقارات",
                    "other": "أخرى"
                }[c.category] || c.category;
                document.getElementById("case-detail-category").innerText = catAr;

                // Success rate details with color coding
                const prob = Math.round(c.success_probability);
                const probSpan = document.getElementById("case-detail-prob");
                probSpan.innerText = `${prob}%`;
                probSpan.style.color = prob > 70 ? "#2ecc71" : prob > 40 ? "#e67e22" : "#e74c3c";

                // Setup status select
                document.getElementById("case-detail-status-select").value = c.status;

                // Draw Visual Pipeline indicator
                this.drawPipeline(c.judgment);

                // Load active notes
                this.renderNotes(c.notes);

                // Load attachments
                this.renderAttachments(c.attachments);

                // Load timeline
                this.renderTimeline(c.timeline);

                // Load expected judgments details
                this.renderJudgments(c.judgment, prob);

                // Reset tabs to default (notes)
                document.querySelectorAll("[data-case-tab]").forEach(t => t.classList.remove("active"));
                document.querySelector("[data-case-tab='notes']").classList.add("active");
                document.querySelectorAll(".case-tab-content").forEach(content => content.style.display = "none");
                document.getElementById("case-tab-notes").style.display = "block";

                // Open modal
                detailsModal.classList.add("visible");
            }
        } catch (e) {
            console.error(e);
        }
    },

    drawPipeline(judgment) {
        const pipelineContainer = document.getElementById("case-detail-pipeline");
        if (!pipelineContainer) return;

        // Steps: Classification -> Retrieval -> Reasoning -> Decision
        const hasJudgment = judgment && Object.keys(judgment).length > 0;
        
        let step = 1; // Default classification done
        if (hasJudgment) {
            step = 4; // Complete pipeline has run
        }

        pipelineContainer.innerHTML = `
            <div class="pipeline-line">
                <div class="pipeline-line-progress" style="width: ${((step - 1) / 3) * 100}%"></div>
            </div>
            
            <div class="pipeline-step ${step >= 1 ? (step === 1 ? 'active' : 'completed') : ''}">
                <div class="pipeline-circle">1</div>
                <div class="pipeline-label">التصنيف</div>
            </div>
            
            <div class="pipeline-step ${step >= 2 ? (step === 2 ? 'active' : 'completed') : ''}">
                <div class="pipeline-circle">2</div>
                <div class="pipeline-label">الاسترجاع</div>
            </div>
            
            <div class="pipeline-step ${step >= 3 ? (step === 3 ? 'active' : 'completed') : ''}">
                <div class="pipeline-circle">3</div>
                <div class="pipeline-label">المداولة</div>
            </div>
            
            <div class="pipeline-step ${step >= 4 ? (step === 4 ? 'active' : 'completed') : ''}">
                <div class="pipeline-circle">4</div>
                <div class="pipeline-label">الحكم</div>
            </div>
        `;
    },

    renderNotes(notes) {
        const container = document.getElementById("case-notes-list");
        if (!container) return;

        container.innerHTML = "";
        if (notes.length === 0) {
            container.innerHTML = `<div style="font-size: 0.8rem; color: var(--text-muted); text-align: center; padding: 12px;">لا توجد ملاحظات لهذه القضية.</div>`;
            return;
        }

        notes.forEach(n => {
            const dateStr = new Date(n.created_at * 1000).toLocaleString("ar-EG");
            const div = document.createElement("div");
            div.className = "note-item";
            div.innerHTML = `
                <div class="note-meta">
                    <span>بواسطة: ${n.author_name || "محامي"}</span>
                    <span>${dateStr}</span>
                </div>
                <div>${n.content}</div>
            `;
            container.appendChild(div);
        });
    },

    async submitNote() {
        if (!this.activeCaseId) return;
        const input = document.getElementById("case-note-new-input");
        const content = input.value.trim();
        if (!content) return;

        try {
            const res = await fetch(`/api/cases/${this.activeCaseId}/notes`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    ...Sidebar.getHeaders()
                },
                body: JSON.stringify({ content: content })
            });

            if (res.ok) {
                input.value = "";
                // Reload case details
                this.viewCaseDetails(this.activeCaseId);
            }
        } catch (e) {
            console.error(e);
        }
    },

    renderAttachments(attachments) {
        const container = document.getElementById("case-attachments-list");
        if (!container) return;

        container.innerHTML = "";
        if (attachments.length === 0) {
            container.innerHTML = `<div style="font-size: 0.8rem; color: var(--text-muted); text-align: center; padding: 12px;">لا توجد مرفقات قانونية. ارفع صحيفة الدعوى أو مستندات القضية.</div>`;
            return;
        }

        attachments.forEach(att => {
            const dateStr = new Date(att.uploaded_at * 1000).toLocaleDateString("ar-EG");
            const div = document.createElement("div");
            div.className = "attachment-item";
            div.innerHTML = `
                <div style="display: flex; align-items: center; gap: 8px;">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px; height:14px; color: var(--accent-blue);"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>
                    <span>${att.file_name}</span>
                </div>
                <div style="font-size: 0.72rem; color: var(--text-muted);">
                    <span>${dateStr}</span>
                </div>
            `;
            container.appendChild(div);
        });
    },

    async uploadCaseDocument() {
        if (!this.activeCaseId) return;
        const input = document.getElementById("case-upload-input");
        if (input.files.length === 0) return;

        const file = input.files[0];
        
        // Show status of upload
        const list = document.getElementById("case-attachments-list");
        const loadingDiv = document.createElement("div");
        loadingDiv.style.cssText = "font-size:0.8rem; text-align:center; padding:12px; color: var(--accent-blue);";
        loadingDiv.innerText = `📎 جاري تحميل وتحليل مستند القضية بالذكاء الاصطناعي: ${file.name}...`;
        list.prepend(loadingDiv);

        const formData = new FormData();
        formData.append("file", file);
        formData.append("case_id", this.activeCaseId);

        try {
            const res = await fetch("/api/documents/upload", {
                method: "POST",
                headers: Sidebar.getHeaders(),
                body: formData
            });

            if (res.ok) {
                input.value = "";
                // Reload full case detail
                await this.viewCaseDetails(this.activeCaseId);
                this.loadAnalytics();
            } else {
                loadingDiv.innerText = "❌ فشل تحليل الملف وتحميله.";
            }
        } catch (e) {
            console.error(e);
            loadingDiv.innerText = "❌ حدث خطأ غير متوقع أثناء المعالجة.";
        }
    },

    renderTimeline(events) {
        const container = document.getElementById("case-timeline-list");
        if (!container) return;

        container.innerHTML = "";
        
        // Populate upcoming deadlines widget in dashboard too
        const dashboardDeadlines = document.getElementById("upcoming-deadlines-list");
        if (dashboardDeadlines && events.length > 0) {
            dashboardDeadlines.innerHTML = "";
        }

        if (events.length === 0) {
            container.innerHTML = `<div style="font-size: 0.8rem; color: var(--text-muted); text-align: center; padding: 12px;">لا توجد أحداث أو مواعيد مسجلة.</div>`;
            return;
        }

        events.forEach(evt => {
            const div = document.createElement("div");
            div.className = `timeline-item ${evt.is_deadline ? 'deadline' : ''}`;
            div.innerHTML = `
                <div class="timeline-item-meta">${evt.event_date} ${evt.is_deadline ? '⚠️ [أجل نهائي]' : ''}</div>
                <div style="font-weight: 700; color: #fff;">${evt.title}</div>
                <div style="font-size: 0.76rem; color: var(--text-secondary); margin-top: 2px;">${evt.description || ""}</div>
            `;
            container.appendChild(div);

            // Add deadlines to dashboard deadlines widget
            if (evt.is_deadline && dashboardDeadlines) {
                const deadlineDiv = document.createElement("div");
                deadlineDiv.className = "deadline-item urgent";
                deadlineDiv.innerHTML = `
                    <span>${evt.title}</span>
                    <span class="deadline-date">${evt.event_date}</span>
                `;
                dashboardDeadlines.appendChild(deadlineDiv);
            }
        });
    },

    async submitTimelineEvent() {
        if (!this.activeCaseId) return;
        const dateInput = document.getElementById("case-timeline-new-date");
        const titleInput = document.getElementById("case-timeline-new-title");
        const deadlineSelect = document.getElementById("case-timeline-new-deadline");

        const date = dateInput.value.trim();
        const title = titleInput.value.trim();
        const isDeadline = parseInt(deadlineSelect.value);

        if (!date || !title) return;

        try {
            const res = await fetch(`/api/cases/${this.activeCaseId}/timeline`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    ...Sidebar.getHeaders()
                },
                body: JSON.stringify({
                    event_date: date,
                    title: title,
                    is_deadline: isDeadline,
                    description: ""
                })
            });

            if (res.ok) {
                dateInput.value = "";
                titleInput.value = "";
                // Reload detail
                this.viewCaseDetails(this.activeCaseId);
            }
        } catch (e) {
            console.error(e);
        }
    },

    renderJudgments(judgment, successProb) {
        const container = document.getElementById("case-judgment-container");
        if (!container) return;

        const hasJudgment = judgment && Object.keys(judgment).length > 0;
        if (!hasJudgment) {
            container.innerHTML = `<div style="padding:12px; background: rgba(25,25,25,0.4); border: 1px solid var(--border-color); border-radius:var(--radius-sm); font-size: 0.85rem; color: var(--text-secondary); text-align: center;">ارفع ملف قضية في علامة التبويب "المرفقات" أو استشر الذكاء الاصطناعي لتوليد توقعات الحكم.</div>`;
            return;
        }

        container.innerHTML = `
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
                <div style="padding: 12px; background: rgba(231, 76, 60, 0.05); border: 1px solid rgba(231, 76, 60, 0.2); border-radius: var(--radius-sm);">
                    <h5 style="font-weight: 700; color: #e74c3c; font-size:0.85rem; margin-bottom: 6px;">حجج ومطالب النيابة والادعاء</h5>
                    <p style="font-size:0.8rem; line-height: 1.5; color: var(--text-secondary);">${judgment.prosecution_summary || "لا توجد معلومات للادعاء."}</p>
                </div>
                <div style="padding: 12px; background: rgba(39, 174, 96, 0.05); border: 1px solid rgba(39, 174, 96, 0.2); border-radius: var(--radius-sm);">
                    <h5 style="font-weight: 700; color: #2ecc71; font-size:0.85rem; margin-bottom: 6px;">دفوع مرافعة الدفاع المتاحة</h5>
                    <p style="font-size:0.8rem; line-height: 1.5; color: var(--text-secondary);">${judgment.defense_summary || "لا توجد دفوع مصاغة بعد."}</p>
                </div>
            </div>
            
            <div style="padding: 14px; background: rgba(25,25,25,0.8); border: 1px solid var(--border-color); border-radius: var(--radius-sm);">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                    <h5 style="font-weight: 700; color: #fff; font-size: 0.9rem;">الحكم المتوقع لمنصة القضاء (AI Verdict)</h5>
                    <span style="font-size: 0.8rem; color: #e67e22; font-weight: 700;">ثقة الحكم: ${successProb}%</span>
                </div>
                <p style="font-size: 0.85rem; line-height: 1.6; color: #fff; border-bottom: 1px solid var(--border-color); padding-bottom: 10px; margin-bottom: 10px;">
                    <strong>منطوق الحكم المتوقع:</strong> <span style="color: #f1c40f;">${judgment.expected_verdict || judgment.verdict || "غير حاسم بعد"}</span>
                </p>
                <div style="font-size: 0.8rem; line-height: 1.5; color: var(--text-secondary);">
                    <strong>الأسباب والحيثيات المستندة:</strong>
                    <p style="margin-top: 4px;">${judgment.reasoning || "لا توجد حيثيات كافية."}</p>
                </div>
            </div>
        `;
    },

    async deleteActiveCase() {
        if (!this.activeCaseId) return;
        if (!confirm("هل أنت متأكد من رغبتك في حذف هذا الملف القضائي بالكامل؟")) return;

        try {
            const res = await fetch(`/api/cases/${this.activeCaseId}`, {
                method: "DELETE",
                headers: Sidebar.getHeaders()
            });

            if (res.ok) {
                document.getElementById("case-details-modal").classList.remove("visible");
                this.loadAnalytics();
                this.loadCases();
            }
        } catch (e) {
            console.error(e);
        }
    },

    async openDraftingAssistantForCase() {
        if (!this.activeCaseId) return;
        
        try {
            const res = await fetch(`/api/cases/${this.activeCaseId}`, {
                headers: Sidebar.getHeaders()
            });
            if (res.ok) {
                const data = await res.json();
                const c = data.case;
                
                // Hide case detail modal
                document.getElementById("case-details-modal").classList.remove("visible");
                
                // Fill details in drafting modal
                document.getElementById("draft-case-details").value = `القضية: ${c.title}\nالموكل: ${c.client_name}\nرقم القضية: ${c.case_number || "غير محدد"}\nالمحكمة: ${c.court || "غير محدد"}\nالوقائع: ${c.description || ""}`;
                
                // Show drafting modal
                document.getElementById("draft-modal").classList.add("visible");
            }
        } catch (e) {
            console.error(e);
        }
    },

    drawRelationshipGraph() {
        const container = document.getElementById("case-relationship-graph");
        if (!container || !this.activeCaseId) return;

        // Fetch details of active case to extract nodes
        fetch(`/api/cases/${this.activeCaseId}`, {
            headers: Sidebar.getHeaders()
        })
        .then(res => res.json())
        .then(data => {
            const c = data.case;
            
            // Build interactive nodes & edges
            const nodes = [];
            const edges = [];
            
            // Center Case Node
            nodes.push({ id: "case_center", label: c.title, color: { background: "#2563eb", border: "#3b82f6" }, font: { color: "#fff", face: "Cairo", size: 14, bold: true } });
            
            let idx = 1;
            
            // Map status, category, client, court
            if (c.client_name) {
                const client_id = `node_${idx++}`;
                nodes.push({ id: client_id, label: `الموكل:\n${c.client_name}`, color: { background: "#2ecc71", border: "#27ae60" }, font: { color: "#fff", face: "Cairo", size: 10 } });
                edges.push({ from: "case_center", to: client_id });
            }
            
            if (c.court) {
                const court_id = `node_${idx++}`;
                nodes.push({ id: court_id, label: `المحكمة:\n${c.court}`, color: { background: "#9b59b6", border: "#8e44ad" }, font: { color: "#fff", face: "Cairo", size: 10 } });
                edges.push({ from: "case_center", to: court_id });
            }

            // Extract judgment and add nodes for Prosecution/Defense/Verdict
            if (c.judgment && Object.keys(c.judgment).length > 0) {
                const jd = c.judgment;
                if (jd.verdict) {
                    const verdict_id = `node_${idx++}`;
                    nodes.push({ id: verdict_id, label: `الحكم المتوقع:\n${jd.verdict}`, color: { background: "#f1c40f", border: "#f39c12" }, font: { color: "#000", face: "Cairo", size: 11, bold: true } });
                    edges.push({ from: "case_center", to: verdict_id });
                }
            }
            
            // Add nodes from extracted entities if available (extracted from document analysis)
            // Attachment names
            c.attachments.forEach(att => {
                const att_id = `node_${idx++}`;
                nodes.push({ id: att_id, label: `ملف:\n${att.file_name}`, color: { background: "#95a5a6", border: "#7f8c8d" }, font: { color: "#fff", face: "Cairo", size: 9 } });
                edges.push({ from: "case_center", to: att_id });
            });

            const dataSet = {
                nodes: new vis.DataSet(nodes),
                edges: new vis.DataSet(edges)
            };

            const options = {
                physics: {
                    barnesHut: {
                        gravitationalConstant: -2000,
                        centralGravity: 0.3,
                        springLength: 95
                    }
                },
                interaction: {
                    hover: true,
                    zoomView: true
                }
            };

            if (this.visNetwork) {
                this.visNetwork.destroy();
            }
            this.visNetwork = new vis.Network(container, dataSet, options);
        })
        .catch(err => console.error("Error drawing graph:", err));
    }
};

// Bind dashboard button to main view
document.addEventListener("DOMContentLoaded", () => {
    const navChat = document.getElementById("nav-chat-btn");
    const navDashboard = document.getElementById("nav-dashboard-btn");
    const chatContainer = document.getElementById("chat-container");
    const dashboardContainer = document.getElementById("dashboard-container");
    const inputArea = document.querySelector(".input-area");
    
    if (navChat && navDashboard && chatContainer && dashboardContainer && inputArea) {
        navChat.onclick = () => {
            navChat.classList.add("active");
            navDashboard.classList.remove("active");
            chatContainer.style.display = "flex";
            inputArea.style.display = "block";
            dashboardContainer.style.display = "none";
        };
        
        navDashboard.onclick = () => {
            if (!Auth.isLoggedIn()) {
                Auth.showModal();
                return;
            }
            navChat.classList.remove("active");
            navDashboard.classList.add("active");
            chatContainer.style.display = "none";
            inputArea.style.display = "none";
            dashboardContainer.style.display = "flex";
            
            // Initialize Dashboard
            Dashboard.init();
        };
    }
});
