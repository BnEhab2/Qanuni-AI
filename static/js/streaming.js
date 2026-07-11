/**
 * Qanuni.AI — SSE Response Stream Handler (v3.0)
 * =============================================
 * Handles POST requests to the streaming endpoints, decodes Server-Sent Events
 * from ReadableStream, and passes parsed objects to UI callbacks.
 */

const Streaming = {
    /**
     * Post a prompt to the backend and stream back SSE chunks.
     * Supports cancellation via AbortController.
     */
    async stream(url, payload, headers, callbacks) {
        const { onSources, onToken, onDone, onError, onClassification, onConfidence, onContradictions, onRisks, onProsecution, onDefense, onJudgeDecision, onPipelineStage, onEntities, onSuccessProbability, onTimeline, onFollowUps } = callbacks;
        
        try {
            const response = await fetch(url, {
                method: "POST",
                headers: headers,
                body: JSON.stringify(payload),
                signal: payload.signal
            });

            if (!response.ok) {
                const text = await response.text();
                let errMsg = "فشل الاتصال بالخادم.";
                try {
                    const errJson = JSON.parse(text);
                    errMsg = errJson.error || errMsg;
                    if (errJson.code === "GUEST_LIMIT_REACHED") {
                        if (callbacks.onLimitReached) {
                            callbacks.onLimitReached();
                            return;
                        }
                    }
                } catch (e) {}
                throw new Error(errMsg);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder("utf-8");
            let buffer = "";

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                
                // Parse Server-Sent Events from buffer (split by double newline)
                let boundary = buffer.indexOf("\n\n");
                while (boundary !== -1) {
                    const eventText = buffer.slice(0, boundary).trim();
                    buffer = buffer.slice(boundary + 2);
                    boundary = buffer.indexOf("\n\n");

                    if (eventText.startsWith("data:")) {
                        const jsonStr = eventText.slice(5).trim();
                        try {
                            const eventData = JSON.parse(jsonStr);
                            this.processEvent(eventData, { onSources, onToken, onDone, onClassification, onConfidence, onContradictions, onRisks, onProsecution, onDefense, onJudgeDecision, onPipelineStage, onEntities, onSuccessProbability, onTimeline, onFollowUps, onError });
                        } catch (e) {
                            console.warn("[Streaming] Failed to parse JSON event:", e, jsonStr);
                        }
                    }
                }
            }
        } catch (error) {
            if (error.name === "AbortError") {
                console.log("[Streaming] Request aborted by user.");
            } else {
                console.error("[Streaming] Error:", error);
                if (onError) onError(error.message);
            }
        }
    },

    processEvent(event, callbacks) {
        switch (event.type) {
            case "sources":
                if (callbacks.onSources) callbacks.onSources(event.data);
                break;
            case "classification":
                if (callbacks.onClassification) callbacks.onClassification(event.data);
                break;
            case "confidence":
                if (callbacks.onConfidence) callbacks.onConfidence(event.data);
                break;
            case "contradictions":
                if (callbacks.onContradictions) callbacks.onContradictions(event.data);
                break;
            case "risks":
                if (callbacks.onRisks) callbacks.onRisks(event.data);
                break;
            case "prosecution":
                if (callbacks.onProsecution) callbacks.onProsecution(event.data);
                break;
            case "defense":
                if (callbacks.onDefense) callbacks.onDefense(event.data);
                break;
            case "judge_decision":
                if (callbacks.onJudgeDecision) callbacks.onJudgeDecision(event.data);
                break;
            case "pipeline_stage":
                if (callbacks.onPipelineStage) callbacks.onPipelineStage(event.data);
                break;
            case "entities":
                if (callbacks.onEntities) callbacks.onEntities(event.data);
                break;
            case "success_probability":
                if (callbacks.onSuccessProbability) callbacks.onSuccessProbability(event.data);
                break;
            case "timeline":
                if (callbacks.onTimeline) callbacks.onTimeline(event.data);
                break;
            case "follow_ups":
                if (callbacks.onFollowUps) callbacks.onFollowUps(event.data);
                break;
            case "token":
                if (callbacks.onToken) callbacks.onToken(event.data);
                break;
            case "done":
                if (callbacks.onDone) callbacks.onDone(event);
                break;
            case "error":
                if (callbacks.onError) callbacks.onError(event.data);
                break;
            default:
                console.log("[Streaming] Unrecognized event type:", event);
        }
    }
};
