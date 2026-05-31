---
title: Qanuni AI
emoji: ⚖️
colorFrom: red
colorTo: gray
sdk: docker
app_port: 7860
---

# Qanuni.AI
### Smart Legal AI Assistant for Egyptian Law
**Qanuni.AI** is a sophisticated, Generative AI-powered legal expert system designed specifically to assist lawyers, judges, and legal consultants in Egypt. It leverages advanced Retrieval-Augmented Generation (RAG) to analyze complex legal cases and derive highly accurate arguments directly from official statutory texts.

---

## Key Features

* **Hybrid Retrieval:** Integrates deep semantic vector search (via ChromaDB using `intfloat/multilingual-e5-base` embeddings) and classical keyword search (via BM25) to retrieve the most precise and contextually relevant statutory articles.
* **Cross-Encoder Reranking:** Employs the state-of-the-art `BAAI/bge-reranker-v2-m3` model to dynamically filter, score, and reorder retrieved candidate documents, ensuring only the most relevant materials are fed to the LLM.
* **Legal Reasoning Chain:** Implements a structured 6-step analytical pipeline (Fact Identification, Legal Domain Classification, Retrieval Context Analysis, Prosecution/Claimant Argument Synthesis, Defense/Mitigation Argument Synthesis, and Final Strategic Conclusion).
* **Interactive Debate Mode:** A revolutionary simulation feature allowing users to practice legal argumentation. The user can assume the role of defense or prosecution while Qanuni.AI plays the opposing side, with all arguments strictly bound and cited to official laws.
* **Persistent Case Memory:** Powered by SQLite database, storing summaries of prior consultations to allow comparison, retrieval of precedents, and detection of legal patterns across cases.
* **Real-time Web Integration:** Features active web search toolkits (via DuckDuckGo) to search for modern amendments, recent Court of Cassation rulings, and current legal news when local database records are insufficient.

---

## Tech Stack

* **Backend:** Flask (Python)
* **LLM Orchestration:** Google ADK (Agent Development Kit) & LiteLLM
* **Models:** GPT-4o-mini (via OpenRouter) / Gemini 1.5 Flash (Fallback)
* **Vector Database:** ChromaDB
* **Embeddings:** `intfloat/multilingual-e5-base`
* **Reranker:** `BAAI/bge-reranker-v2-m3`
* **Case Database:** SQLite

---

## Setup Guide

### 1. Prerequisites
Ensure you have Python 3.10 or later installed on your system.

### 2. Installation
```bash
# Clone the repository
git clone https://github.com/BnEhab2/Qanuni-AI.git
cd Qanuni-AI

# Create a virtual environment
python -m venv venv

# Activate the virtual environment
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# Install the required packages
pip install -r requirements.txt
```

### 3. Environment Variables
Create a `.env` file in the root directory (refer to `.env.example` as a template) and add your API keys:
```env
OPENROUTER_API_KEY=your_openrouter_api_key
GEMINI_API_KEY=your_gemini_api_key
```

### 4. Run the Server
```bash
python app.py
```
The server will start running locally at: `http://localhost:5000`

---

## Project Structure
```text
Qanuni-AI/
│
├── core/                  # Core RAG, tools, and reasoning components
│   ├── memory.py          # SQLite Case Memory implementation
│   ├── reasoning.py       # 6-Step Legal Reasoning Chain
│   ├── reranker.py        # Cross-Encoder model integration
│   ├── retrieval.py       # Hybrid search pipeline (Semantic + BM25)
│   ├── tools.py           # Core agent tools
│   └── web_search.py      # Real-time search tools
│
├── templates/             # Frontend view
│   └── index.html         # Interactive chat interface
│
├── app.py                 # Flask app & API endpoints
├── config.py              # Centralized configuration & constants
├── requirements.txt       # Dependencies
└── .gitignore             # Git ignore file
```

---

## Disclaimer
The information and analyses provided by **Qanuni.AI** are for informational and research purposes only. This system is not a substitute for formal, qualified professional legal advice from a licensed attorney.
