from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    status,
    APIRouter,
    WebSocket,
    WebSocketDisconnect,
)

# Leave Meeting
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from database.database import get_db
from database.models import Base, User
from .utils import generate_unique_id
from .models import Meeting, MeetingCreate, MeetingJoin, MeetingLeave
import os
import base64
import json
from typing import Dict, Any, Optional, List
from pydantic import BaseModel
import tempfile
import aiofiles


app = FastAPI()

HOST_URL = os.getenv("HOST_URL")

router = APIRouter()

origins = [
    f"{HOST_URL}:3000",
    f"{HOST_URL}:3001",
    "*",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

meetings: Dict[str, Meeting] = {}


@router.get("/")
async def get_all_meetings():
    return [meeting for meeting in meetings.values()]


@router.get("/{meeting_id}", response_model=Meeting)
async def get_meeting(meeting_id: str):
    if meeting_id not in meetings:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meetings[meeting_id]


@router.post("/create", response_model=Meeting)
async def create_meeting(meeting: MeetingCreate):
    meeting_id = generate_unique_id()
    new_meeting = Meeting(
        id=meeting_id,
        name=meeting.meeting_name,
        creator_id=meeting.creator_id,
        participants=[meeting.creator_id],
    )
    meetings[meeting_id] = new_meeting
    return new_meeting


@router.post("/join", response_model=Meeting)
async def join_meeting(meeting_join: MeetingJoin):
    if meeting_join.meeting_id not in meetings:
        raise HTTPException(status_code=404, detail="Meeting not found")

    meeting = meetings[meeting_join.meeting_id]
    if meeting_join.creator_id in meeting.participants:
        raise HTTPException(status_code=401, detail="user is already in meeting")

    meeting.participants.append(meeting_join.creator_id)

    return meeting


@router.post("/leave", response_model=Meeting)
async def leave_meeting(meeting_leave: MeetingLeave):
    if meeting_leave.meeting_id not in meetings:
        raise HTTPException(status_code=404, detail="Meeting not found")

    meeting = meetings[meeting_leave.meeting_id]

    if meeting_leave.user_id not in meeting.participants:
        raise HTTPException(status_code=400, detail="User is not in this meeting")

    meeting.participants.remove(meeting_leave.user_id)

    if not meeting.participants:
        del meetings[meeting_leave.meeting_id]
        raise HTTPException(
            status_code=200, detail="Meeting deleted as last participant left"
        )

    if meeting_leave.user_id == meeting.creator_id and meeting.participants:
        meeting.creator_id = meeting.participants[0]

    return meeting


class ConnectionManager:
    def __init__(self):
        """keep list of active connections for each meeting"""
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, meeting_id: str):
        await websocket.accept()
        if meeting_id not in self.active_connections:
            self.active_connections[meeting_id] = []
        self.active_connections[meeting_id].append(websocket)

    def disconnect(self, websocket: WebSocket, meeting_id: str):
        self.active_connections[meeting_id].remove(websocket)

    async def broadcast(self, message: dict, meeting_id: str):
        for connection in self.active_connections[meeting_id]:
            await connection.send_json(message)


manager = ConnectionManager()


class SharedContent(BaseModel):
    llm_answer: str = ""
    username: str = ""
    user_msg: str = ""
    screen_image: str = ""


class SharedState(BaseModel):
    content: SharedContent = SharedContent()


shared_states: Dict[str, SharedState] = {}


async def update_shared_state(meeting_id: str, content: SharedContent):
    if meeting_id not in shared_states:
        shared_states[meeting_id] = SharedState()
    shared_states[meeting_id].content = content
    await manager.broadcast(
        {"type": "state_update", "content": content.dict()}, meeting_id
    )


class LLMResponse(BaseModel):
    screen_image: str
    llm_answer: str


async def dummy_llm_response(query: str) -> LLMResponse:
    async with aiofiles.open("app/graph.png", mode="rb") as file:
        image_data = await file.read()

    return LLMResponse(
        screen_image=base64.b64encode(image_data).decode("utf-8"),
        llm_answer=f"Dummy response to: {query}",
    )


async def process_message(data: dict, meeting_id: str, username: str):
    if data["type"] == "text_query":
        llm_response = await dummy_llm_response(data["query"])
        updated_content = SharedContent(
            llm_answer=llm_response.llm_answer if llm_response else None,
            username=username,
            user_msg=data["query"],
            screen_image=llm_response.screen_image if llm_response else None,
        )
        await update_shared_state(meeting_id, updated_content)


@app.websocket("/ws/{meeting_id}/{username}")
async def websocket_endpoint(websocket: WebSocket, meeting_id: str, username: str):
    await manager.connect(websocket, meeting_id)
    try:
        if meeting_id in shared_states:
            await websocket.send_json(
                {
                    "type": "state_update",
                    "content": shared_states[meeting_id].content.dict(),
                }
            )

        while True:
            data = await websocket.receive_json()
            await process_message(data, meeting_id, username)
    except WebSocketDisconnect:
        manager.disconnect(websocket, meeting_id)
    except Exception as e:
        print(f"Error in websocket: {str(e)}")
        manager.disconnect(websocket, meeting_id)


app.include_router(router, prefix="/api/meeting")
