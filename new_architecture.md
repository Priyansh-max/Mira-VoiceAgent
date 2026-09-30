Architecture to build
Use this split:
Frontend
microphone capture
WebRTC connection to OpenAI Realtime
receive assistant audio directly
render live transcript + trace panel
FastAPI backend
create short-lived realtime session / token
own business logic and tool execution
keep conversation/session metadata
collect and expose trace events
optionally keep a server-side control channel later
Practical flow
Frontend asks FastAPI for a realtime session
FastAPI calls OpenAI to create the session
Frontend gets ephemeral/session credentials
Frontend opens WebRTC with OpenAI
User speaks directly to the realtime model
Model responds with speech directly to frontend
When tool use is needed, your backend executes tools
Trace events are shown in your trace panel
Why this is best
Lowest latency
fewer network hops for audio
more natural interruptions and turn-taking
backend still remains important for tools, trace, and app logic
What changes from your current backend
Your current backend is built around:
REST /chat
REST /stt
REST /tts
local rule-based orchestration
For Option 1, the important backend pieces become:
POST /realtime/session
creates OpenAI realtime session / ephemeral auth
POST /tool/<name> or internal tool handlers
backend executes things like lookup_ticket
trace/session endpoints
keep your trace system because that is still valuable
The current stt.py and tts.py become much less important, because OpenAI Realtime handles audio in/out.
Recommended backend structure now
I’d reshape toward this:
main.py
realtime.py
tools.py
trace.py
conversation.py
agent_tools.py or tool_handlers.py
What each should do:
main.py
FastAPI app
routes
realtime.py
create realtime session with OpenAI
any OpenAI session config
voice/model/instructions/tools schema
tools.py
simulated support tools like ticket lookup, callback scheduling
trace.py
same purpose as now
record session lifecycle, tool calls, tool results, notable agent events
conversation.py
lightweight session state for your app
user name, ticket id, history summary if needed
Best implementation order
Stop expanding old REST STT/TTS
don’t invest more in stt.py / tts.py
Add POST /realtime/session
backend creates OpenAI realtime session
returns client connection info
Frontend connects to OpenAI with WebRTC
mic in
assistant audio out
Show basic live conversation
no tools yet
just prove natural voice loop works
Add tool calling
ticket lookup
order status
schedule callback
Send trace events into UI
session created
user spoke
intent/tool requested
tool result
assistant response