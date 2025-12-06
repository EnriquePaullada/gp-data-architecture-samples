"""
Conversation State Models - Pydantic Schemas

This module demonstrates production-grade state management for multi-turn
AI conversations, including message history, scheduling logic, and
follow-up tracking.

Architecture Pattern: Type-safe state machines with validation at boundaries.
"""

from pydantic import BaseModel, Field, field_validator
from typing import List, Literal, Optional
from datetime import datetime
from dateutil import parser
import pytz


# ============================================================================
# MESSAGE MODELS
# ============================================================================

class ConversationMessage(BaseModel):
    """
    Individual message in a conversation history.
    
    Architecture Decision: Store both role and content to maintain
    full conversation context for AI model consumption. Timestamp
    enables conversation flow analysis and timeout handling.
    """
    role: Literal["user", "assistant"]
    content: str
    timestamp: str  # ISO 8601 format
    
    class Config:
        """Pydantic configuration for better JSON serialization."""
        json_schema_extra = {
            "example": {
                "role": "user",
                "content": "I'm interested in your services",
                "timestamp": "2025-01-15T10:30:00Z"
            }
        }


# ============================================================================
# SCHEDULING MODELS
# ============================================================================

class ScheduleFollowupRequest(BaseModel):
    """
    Request model for scheduling follow-up actions.
    
    Architecture Pattern: Strict validation ensures scheduled times are
    always valid and in the future. This prevents logic errors in the
    scheduling system.
    
    Key Design Decision: Require timezone information to handle
    international conversations correctly.
    """
    lead_id: str = Field(..., description="Unique identifier for the lead")
    schedule_time: str = Field(..., description="ISO 8601 datetime string with timezone")
    followup_reason: str = Field(..., description="Reason for follow-up (e.g., 'demo_scheduled')")
    last_4_turns: Optional[List[str]] = None  # Last 4 conversation turns for context
    
    @field_validator('schedule_time')
    @classmethod
    def validate_schedule_time(cls, v: str) -> str:
        """
        Validate that schedule time is:
        1. Valid ISO 8601 format
        2. Includes timezone information
        3. Is in the future
        
        Architecture Decision: Fail fast on invalid scheduling data
        rather than silently accepting bad input that causes issues later.
        """
        try:
            dt = parser.isoparse(v)
        except Exception as e:
            raise ValueError(f"Invalid ISO 8601 datetime: {e}")
        
        # Ensure timezone information is present
        if dt.tzinfo is None:
            raise ValueError("Schedule time must include timezone information.")
        
        # Ensure schedule time is in the future
        if dt <= datetime.now(pytz.utc):
            raise ValueError("Schedule time must be in the future")
        
        return v


class ScheduleFollowupResponse(BaseModel):
    """
    Response model for scheduled follow-up confirmation.
    
    Returns both the scheduled task details and confirmation of
    whether any previous scheduled tasks were cancelled.
    """
    status: str = Field(..., description="Status of the scheduling operation")
    task_id: str = Field(..., description="Unique identifier for the scheduled task")
    scheduled_for: str = Field(..., description="Confirmed schedule time (ISO 8601)")
    cancelled_previous: bool = Field(..., description="Whether a previous task was cancelled")


class ExecuteFollowupRequest(BaseModel):
    """
    Request model for executing a scheduled follow-up.
    
    Contains the lead context and conversation history needed
    to generate a contextual follow-up message.
    """
    lead_id: str
    followup_reason: str
    conversation_context: List[ConversationMessage] = Field(
        ..., 
        description="Recent conversation history for context"
    )


# ============================================================================
# CONVERSATION CONTEXT MODELS
# ============================================================================

class LeadConversationState(BaseModel):
    """
    Complete conversation state for a lead.
    
    Architecture Pattern: Single source of truth for conversation state.
    All state transitions go through this model, ensuring consistency
    and enabling audit trails.
    
    Key Design: Separate 'conversation_history' (full log) from
    'active_context' (what the AI sees) to manage token limits while
    preserving full history for analysis.
    """
    lead_id: str
    phone_number: str
    language: Literal["en", "es"] = "en"  # Bilingual support
    
    conversation_history: List[ConversationMessage] = Field(
        default_factory=list,
        description="Complete conversation history"
    )
    
    active_context: List[ConversationMessage] = Field(
        default_factory=list,
        description="Recent messages for AI context (token-limited)"
    )
    
    # State tracking
    current_stage: str = Field(
        default="initial_contact",
        description="Current conversation stage (e.g., 'qualification', 'scheduling')"
    )
    
    scheduled_followup: Optional[str] = Field(
        default=None,
        description="ISO 8601 datetime of next scheduled follow-up"
    )
    
    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    def add_message(self, role: str, content: str) -> None:
        """
        Add a message to conversation history and update active context.
        
        Architecture Decision: Encapsulate context window management
        in the model itself. Keeps business logic DRY and ensures
        consistent behavior across all conversation updates.
        """
        message = ConversationMessage(
            role=role,
            content=content,
            timestamp=datetime.utcnow().isoformat() + "Z"
        )
        
        # Add to full history
        self.conversation_history.append(message)
        
        # Update active context (keep last N messages to manage token limits)
        MAX_ACTIVE_CONTEXT = 10  # Configurable based on token budget
        self.active_context.append(message)
        if len(self.active_context) > MAX_ACTIVE_CONTEXT:
            self.active_context = self.active_context[-MAX_ACTIVE_CONTEXT:]
        
        # Update timestamp
        self.updated_at = datetime.utcnow()
    
    def get_context_for_ai(self) -> List[dict]:
        """
        Format active context for AI model consumption.
        
        Returns messages in the format expected by OpenAI API:
        [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
        """
        return [
            {"role": msg.role, "content": msg.content}
            for msg in self.active_context
        ]


# ============================================================================
# BANT QUALIFICATION MODEL
# ============================================================================

class BANTQualification(BaseModel):
    """
    BANT (Budget, Authority, Need, Timing) qualification scoring.
    
    Architecture Pattern: Structured qualification ensures consistent
    lead scoring across all agent interactions. Scores can be used
    for routing, prioritization, and reporting.
    """
    budget_score: int = Field(ge=0, le=10, description="Budget qualification (0-10)")
    authority_score: int = Field(ge=0, le=10, description="Authority qualification (0-10)")
    need_score: int = Field(ge=0, le=10, description="Need qualification (0-10)")
    timing_score: int = Field(ge=0, le=10, description="Timing qualification (0-10)")
    
    overall_score: int = Field(ge=0, le=10, description="Weighted overall score")
    qualification_level: Literal["high", "medium", "low"] = Field(
        ...,
        description="Qualification tier based on overall score"
    )
    
    reasoning: str = Field(..., description="Explanation of qualification scoring")
    
    @classmethod
    def calculate_overall(cls, b: int, a: int, n: int, t: int) -> int:
        """
        Calculate weighted overall BANT score.
        
        Architecture Decision: Need and Timing weighted higher (30% each)
        as they're stronger indicators of near-term conversion.
        Budget and Authority at 20% each.
        """
        weights = {"budget": 0.2, "authority": 0.2, "need": 0.3, "timing": 0.3}
        overall = int(
            b * weights["budget"] +
            a * weights["authority"] +
            n * weights["need"] +
            t * weights["timing"]
        )
        return min(10, max(0, overall))  # Ensure 0-10 range


# ============================================================================
# KEY ARCHITECTURE DECISIONS
# ============================================================================

"""
1. TYPE SAFETY EVERYWHERE
   - Pydantic enforces schema validation at API boundaries
   - Prevents invalid data from entering the system
   - Catches errors early, close to the source

2. EXPLICIT STATE MACHINES
   - Conversation state transitions are explicit and auditable
   - Current stage tracking enables conditional logic in agents
   - Full history preserved while managing token limits

3. TIMEZONE AWARENESS
   - All datetime handling includes timezone information
   - Critical for international B2B operations
   - Prevents scheduling errors across time zones

4. CONTEXT WINDOW MANAGEMENT
   - Separate full history from active context
   - Token limit management built into the model
   - Preserves conversation coherence while controlling costs

5. BILINGUAL SUPPORT
   - Language field enables Spanish/English switching
   - Conversation context preserved across language changes
   - Auto-detection happens at message level, stored at conversation level

6. VALIDATION AT BOUNDARIES
   - Field validators catch errors immediately
   - Business rules encoded in model validation
   - Fail fast principle: reject bad data early

7. AUDIT TRAIL
   - Timestamps on all messages and state changes
   - Full conversation history retained
   - Enables analysis of agent performance and conversation flow
"""
