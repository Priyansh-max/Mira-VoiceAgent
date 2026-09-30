Project Document
Voice Agent with Tool Orchestration & Trace Visualization
1. Project Overview

This project implements a voice-enabled AI agent capable of understanding user requests, managing conversation context, detecting intent and sentiment, calling backend tools, and producing a spoken response.

The system also provides a real-time trace of the agent’s reasoning and tool execution, allowing developers to observe how the agent processes requests internally.

The goal of this project is to demonstrate the architecture behind modern voice agents used in customer support and AI assistants.

This prototype will simulate a customer support voice assistant capable of:

understanding spoken input

identifying user intent

tracking conversation state

calling backend services (tools)

generating responses

responding with synthesized speech

displaying internal agent reasoning in a trace panel

2. System Capabilities

The system will support the following features:

Voice Interaction

Users interact with the system through voice input captured in the browser.

Speech-to-Text

User speech is converted into text using a speech recognition model.

Conversation Tracking

The system maintains a session state storing user information and conversation context.

Intent Detection

The agent determines the user’s intent based on the spoken request.

Sentiment Detection

The system analyzes user sentiment and adjusts responses accordingly.

Tool Calling

The agent can call backend tools to retrieve information or perform actions.

Agent Trace Visualization

Every reasoning step and tool call is recorded and displayed in the UI.

Text-to-Speech

The agent converts responses back into spoken audio.

3. Example Conversation Flow

Example user interaction:

User says:

"Hi this is John, check my ticket 4821"

System actions:

User Speech
↓
Speech-to-Text
↓
Intent Detection → lookup_ticket
↓
Extracted ticket ID → 4821
↓
Tool Call → lookup_ticket(4821)
↓
Tool Result → refund pending
↓
Response Generation
↓
Text-to-Speech

Agent responds:

"Hello John. I found your ticket 4821. Your refund is currently pending. Would you like me to schedule a callback?"

4. System Architecture
Browser (React UI)
        ↓
Microphone Input
        ↓
Speech-to-Text
        ↓
FastAPI Backend
        ↓
Conversation Manager
        ↓
Intent + Sentiment Detection
        ↓
Agent Orchestrator
        ↓
Tool Registry
        ↓
Tool Execution
        ↓
Trace Logger
        ↓
Response Generation
        ↓
Text-to-Speech
        ↓
Audio Playback
5. Technology Stack
Backend

Language
Python

Framework
FastAPI

Libraries

fastapi
uvicorn
openai
pydantic
python-multipart

Responsibilities

speech-to-text handling

conversation state management

intent detection

sentiment detection

tool execution

agent reasoning

trace generation

text-to-speech

Frontend

Framework
React

Build Tool
Vite

Responsibilities

microphone recording

sending audio to backend

displaying trace events

playing synthesized audio responses

6. Core Components
Conversation Manager

Stores session data during the conversation.

Example session state:

session = {
    "user_name": None,
    "verified": False,
    "ticket_id": None,
    "sentiment": None,
    "conversation_history": []
}

Responsibilities

maintain conversation memory

track user identity

store conversation context

7. Intent Detection

The system determines the user's goal.

Supported intents:

lookup_ticket
order_status
schedule_callback
general_query

Example intent output:

{
  "intent": "lookup_ticket",
  "ticket_id": "4821"
}
8. Sentiment Detection

The system evaluates user tone.

Possible sentiments:

positive
neutral
negative

Example:

User says:

"This delay is really frustrating"

Detected sentiment:

negative

Response adaptation:

"I'm sorry for the delay. Let me check that for you."

9. Tool Registry

The agent can call backend tools.

Tools simulate backend services.

Tool 1: Ticket Lookup
lookup_ticket(case_id)

Example result:

{
 "case_id": "4821",
 "status": "Refund pending",
 "priority": "High"
}
Tool 2: Order Status
get_order_status(order_id)

Example result:

{
 "order_id": "1234",
 "status": "Shipped today"
}
Tool 3: Schedule Callback
schedule_callback(time)

Example result:

{
 "status": "Callback scheduled",
 "time": "Tomorrow"
}
10. Agent Orchestrator

The orchestrator manages the reasoning loop.

Agent loop:

User Input
↓
Intent Detection
↓
Tool Selection
↓
Tool Execution
↓
Tool Result
↓
Response Generation

Responsibilities

interpret user intent

select appropriate tool

execute tool

construct response

11. Trace System

Every step in the reasoning process is recorded.

Example trace output:

User Input
↓
Intent Detected: lookup_ticket
↓
Sentiment: neutral
↓
Tool Called: lookup_ticket(4821)
↓
Tool Result: refund pending
↓
Response Generated

The frontend displays this information in real time.

12. Project Folder Structure

Backend structure

backend
│
├── main.py
├── agent.py
├── tools.py
├── trace.py
├── conversation.py
├── intent.py
└── sentiment.py

Frontend structure

frontend
│
├── src
│   ├── App.jsx
│   ├── components
│   │   ├── VoiceRecorder.jsx
│   │   ├── TracePanel.jsx
│   │   └── ChatPanel.jsx
│   └── api.js
13. Demo Scenario

Example demonstration during interview.

User says:

"Hi this is John, check ticket 4821"

Trace shows:

User Identified: John
Intent Detected: lookup_ticket
Tool Called: lookup_ticket
Tool Result: refund pending
Response Generated

User then says:

"Schedule callback tomorrow"

Trace shows:

Intent Detected: schedule_callback
Tool Called: schedule_callback
Result: scheduled

Agent speaks confirmation.