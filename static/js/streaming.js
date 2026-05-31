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
        const { onSources, onToken, onDone, onError, onClassification } = callbacks;
        
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
                            this.processEvent(eventData, { onSources, onToken, onDone, onClassification });
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
