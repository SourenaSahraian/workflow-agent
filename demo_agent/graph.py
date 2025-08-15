from pydantic import BaseModel
from typing import Annotated, List, Literal
from enum import Enum
from langchain_openai import ChatOpenAI
from langchain_core.messages import (
    BaseMessage,
    SystemMessage,
    AIMessage,
    ToolMessage
)
from langgraph.types import Command, interrupt
from langgraph.graph.message import add_messages
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver
from langchain_mcp_adapters.client import MultiServerMCPClient
import json
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from config import mcp_config


class ReviewAction(Enum):
    """Enum for human review actions."""
    CONTINUE = "continue"
    c= "continue"
    UPDATE = "update"
    FEEDBACK = "feedback"
    REJECT = "reject"
    EXIT = "exit"


class PrismaAgentState(BaseModel):
    """The state of the Prisma agent.
    
    Attributes:
        messages: The list of messages in the conversation.
        protected_tools: The list of tools that require human review.
        yolo_mode: true means be a little rouge .
    """
    messages: Annotated[List[BaseMessage], add_messages] = []
    protected_tools: List[str] = [
        "executeQuery"
    ]
    yolo_mode: bool = True


def build_agent_graph(tools: List = [], resources: List = [], companyId: str = "unknown"):
    """
    Build the LangGraph application with provided tools and resources.
    """
    
    # Build resources info for system prompt
    resources_info = ""
    if resources:
        resources_info = "\n\nAvailable Database Schema:\n"
        for resource in resources:
            if hasattr(resource, 'data') and resource.data:
                resources_info += f"{resource.data}\n"
            elif hasattr(resource, 'uri'):
                resources_info += f"- {resource.uri}: Schema resource available\n"
            else:
                resources_info += f"- {resource}: Resource available\n"
    system_prompt = f"""You are CT agent, a helpful AI assistant specialized in generating and executing SQL queries against a PostgreSQL database using Prisma schema context.
   


IMPORTANT INSTRUCTIONS:
0. ** always start by saying ,Hello, I’m your humble agent. My boy, Soorena is busy training me to get smarter and more capable. I’m an AI agent with attitude,
 I’m still a young project, but he says this kid’s going places—so bear with me.
 Now, how can I help you with your database or SQL queries today?
1. **Database Schema Information**: The complete database schema is provided below in the "Available Database Schema" section. Use this information to understand:
   - Available tables and their relationships
   - Field mappings (e.g., companyTaxYearId maps to company_tax_year_id in the database)
   - Data types and constraints
   - Primary keys and foreign key relationships

2. **Field Mapping Awareness**: The Prisma schema uses camelCase field names that map to snake_case database columns:
   - companyTaxYearId → company_tax_year_id
   - entityRecordId → entity_record_id  
   - recordId → record_id
   - Always use the database column names (snake_case) in your SQL queries

3. **When users ask about database structure** (tables, columns, relationships):
   - Refer to the schema information provided below
   - DO NOT run SQL queries like "SHOW TABLES" or "DESCRIBE table"
   - DO NOT use read_file to access schema files
   - Extract the information from the provided schema and present it clearly

4. **Primary Table**: The main table you'll work with is the "filings" table, which contains comprehensive filing information.

5. **Company Context**: Always check for companyId: {companyId}
   - If companyId is "unknown", prompt the user to provide their company ID
   - When querying company-specific data, always filter by the appropriate company field

6. **File Operations**: When saving files (like reports, results, etc.):
   - ALWAYS use the /projects/ prefix for file paths
   - Example: save to "/projects/report.txt" NOT "report.txt" or "/app/report.txt"
   - The filesystem is containerized and only allows access to the /projects directory

    {resources_info}

            <tools>
            {{tools}}
            </tools>

Generate appropriate SQL queries for user requests and ALWAYS seek human approval before executing them."""   
    
    llm = ChatOpenAI(
        model="gpt-4.1-mini-2025-04-14",
        temperature=0.1,
    )
    if tools:
        llm = llm.bind_tools(tools, parallel_tool_calls=False)
        # inject tools into system prompt
        tools_json = [tool.model_dump_json(include=["name", "description"]) for tool in tools]
        system_prompt = system_prompt.format(tools="\n".join(tools_json), companyId=companyId)
    else:
        # Format with empty tools and companyId
        system_prompt = system_prompt.format(tools="", companyId=companyId)


    def assistant_node(state: PrismaAgentState) -> PrismaAgentState:
        response = llm.invoke(
            [SystemMessage(content=system_prompt)] +
            state.messages
            )
        state.messages = state.messages + [response]
        return state
    
    async def human_query_review_node(state: PrismaAgentState) -> Command[Literal["assistant_node", "tools"]]:
        """Human review node for database query execution approval"""

        last_message = state.messages[-1]

        # Ensure we have a valid AI message with tool calls
        if not isinstance(last_message, AIMessage) or not last_message.tool_calls:
            raise ValueError("human_query_review_node called without valid tool calls")

        tool_call = last_message.tool_calls[-1]

        # Stop graph execution at this node and wait for human input
        human_review: dict = interrupt({
            "message": "Query execution requires your approval:",
            "tool_call": tool_call,
            "query_info": {
                "type": "SQL Query",
                "description": tool_call.get("args", {}).get("description", "Database query"),
                "query": tool_call.get("args", {}).get("sql_query", "No query provided")
            },
            "original_request": state.messages[0].content if state.messages else "No original request"
        })

        review_action = human_review.get("action")
        review_data = human_review.get("data")

        match review_action:
            case ReviewAction.CONTINUE.value | "c" | "C":
                # Approve the query execution as-is
                return Command(goto="tools")
                
            case ReviewAction.UPDATE.value:
                # Update the query parameters
                if review_data is None:
                    raise ValueError("update action requires data")

                updated_message = AIMessage(
                    content=last_message.content,
                    tool_calls=[{
                        "id": tool_call["id"],
                        "name": tool_call["name"],
                        "args": json.loads(review_data)
                    }],
                    id=last_message.id
                )
                return Command(goto="tools", update={"messages": [updated_message]})
                
            case ReviewAction.FEEDBACK.value:
                # Send feedback to the Agent
                if review_data is None:
                    raise ValueError("feedback action requires data")

                tool_message = ToolMessage(
                    content=review_data,
                    name=tool_call["name"],
                    tool_call_id=tool_call["id"]
                )
                return Command(goto="assistant_node", update={"messages": [tool_message]})
                
            case ReviewAction.REJECT.value:
                # Reject the query execution
                tool_message = ToolMessage(
                    content="The query execution was rejected by the user. Please ask for clarification or suggest alternative approaches.",
                    name=tool_call["name"],
                    tool_call_id=tool_call["id"]
                )
                return Command(goto="assistant_node", update={"messages": [tool_message]})
                
            case _:
                #TODO: Handle we should probaably reject or somehting
                # Default: continue with execution
                return Command(goto="tools")

    async def assistant_router(state: PrismaAgentState) -> str:
        last_message = state.messages[-1]
        if isinstance(last_message, AIMessage) and last_message.tool_calls:
            if not state.yolo_mode:
                if any(tool_call["name"] in state.protected_tools for tool_call in last_message.tool_calls):
                    return "human_query_review_node"
            return "tools"
        else:
            return END

    builder = StateGraph(PrismaAgentState)

    builder.add_node(assistant_node)
    builder.add_node(human_query_review_node)
    builder.add_node("tools", ToolNode(tools))

    builder.add_edge(START, "assistant_node")
    builder.add_conditional_edges("assistant_node", assistant_router, ["tools", "human_query_review_node", END])
    builder.add_edge("tools", "assistant_node")

    # Checkpointing is required for human-in-the-loop!
    return builder.compile(checkpointer=MemorySaver())


def compile_and_display_graph():
    """Compile the agent graph and display its structure."""
    print(" Compiling agent graph...")
    graph = build_agent_graph()  # No tools needed for structure visualization
    
    try:
        # Save as PNG file
        graph_png = graph.get_graph().draw_mermaid_png()
        with open("agent_graph.png", "wb") as f:
            f.write(graph_png)
        print(" Graph compiled successfully!")
        print(" Graph visualization saved as 'agent_graph.png'")
    except Exception as e:
        print(f"⚠️ Error occuring in  visualizing the graph: {e}")
    
    # Print the mermaid diagram as text
    print("\n🔍 Graph structure (Mermaid syntax):")
    print(graph.get_graph().draw_mermaid())
    
    return graph


# ===== SELECT AND RUN THIS SECTION =====

from dotenv import load_dotenv
load_dotenv()
graph = build_agent_graph()
print("✅ Graph built!")
print("🔍 Mermaid syntax:")
print(graph.get_graph().draw_mermaid())

# ===== not sure why the comments makes it work :() =====


# visualize graph
if __name__ == "__main__":
    from IPython.display import display, Image
    from dotenv import load_dotenv
    load_dotenv()

    graph = build_agent_graph()
    display(Image(graph.get_graph().draw_mermaid_png()))