#!/usr/bin/env python3
"""
Interactive Chat Interface for Prisma Query Agent

A simple command-line interface for natural language database queries
with human-in-the-loop approval for query execution.
"""

import os

from dotenv import load_dotenv
load_dotenv()


# LangSmith tracing is controlled by environment variables in .env file
yolo_mode=os.environ.get("YOLLO_MODE", "False")

companyId = os.environ.get("COMPANY_ID", "unknown")

print(f"yolo_mode ----------: {yolo_mode} ")
print(f"LANGCHAIN_TRACING_V2: {os.environ.get('LANGCHAIN_TRACING_V2', 'Not set')} ")
print(f"LANGSMITH_API_KEY: {os.environ.get('LANGSMITH_API_KEY', 'Not set')[:10]}...")
print(f"LANGCHAIN_PROJECT: {os.environ.get('LANGCHAIN_PROJECT', 'Not set')} ")

# Now we can use proper package imports with __init__.py files
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.graph import StateGraph
from langchain_core.messages import HumanMessage, AIMessageChunk
from typing import AsyncGenerator
from config import mcp_config
from demo_agent import build_agent_graph, PrismaAgentState, ReviewAction
from langgraph.types import Command;
from langchain_core.runnables.config import RunnableConfig
import json


print(f"companyId loaded from env: {companyId} ")




async def stream_graph_response(
        input: PrismaAgentState | Command, graph: StateGraph, config: dict = {}
        ) -> AsyncGenerator[str, None]:
    """
    Stream the response from the graph while parsing out tool calls.
    """
    async for message_chunk, metadata in graph.astream(
        input=input,
        stream_mode="messages",
        config=config
        ):
        if isinstance(message_chunk, AIMessageChunk):
            if message_chunk.response_metadata:
                finish_reason = message_chunk.response_metadata.get("finish_reason", "")
                if finish_reason == "tool_calls":
                    yield "\n"

            if message_chunk.tool_call_chunks:
                tool_chunk = message_chunk.tool_call_chunks[0]
                tool_name = tool_chunk.get("name", "")
                args = tool_chunk.get("args", "")
                
                if tool_name:
                    yield f"\n[TOOL: {tool_name}]\n"
                    
                if args:
                    yield args
            else:
                yield message_chunk.content
            continue


def print_welcome():
    """Print welcome message."""
    print("Prisma Query Agent")
    print("Type 'help' for commands or 'exit' to quit")


def print_approval_instructions():
    """Please specify whether you want to reject, continue, update, or provide feedback"""
    print("\nApproval options:")
    print("continue - Execute the query or c for short!")
    print("reject - Cancel execution") 
    print("update - Modify the query")
    print("feedback - Provide feedback")
    print("exit - Quit session")


async def handle_human_approval(interrupt_data: dict) -> dict:
    """Handle human approval interaction with enhanced formatting."""
    
    print("\n⚠️  HUMAN APPROVAL REQUIRED ⚠️\n")
    
    # Display the message from the interrupt
    print(interrupt_data.get("message", "Query execution requires your approval:"))
    
    # Extract and display query information
    query_info = interrupt_data.get("query_info", {})
    tool_call = interrupt_data.get("tool_call", {})
    
    print(f"\nTool: {tool_call.get('name', 'unknown')}")
    print(f"Query Type: {query_info.get('type', 'unknown')}")
    print(f"Description: {query_info.get('description', 'No description')}")
    
    query = query_info.get('query', 'No query provided')
    print(f"\nGenerated Query:")
    print(f"{query}\n")
    print_approval_instructions()
    action = ""
    data = None
    

    #ok, this is important as if user f es up the action, we'll be routed to lala land by the graph
    valid_actions = [action.value for action in ReviewAction]
    while action not in valid_actions:
        print("Invalid action. Please try again." if action else "")
        
        action = input(f"Action ({', '.join(valid_actions)}): ").strip().lower()
    
    if action == ReviewAction.EXIT.value:
        return {"action": "exit"}

    # Get additional data for update and feedback actions
    if action == ReviewAction.UPDATE.value:
        print("\nUpdate Instructions:")
        print("Provide the new query parameters as JSON, or describe the changes you want:")
        data = input("Update data: ").strip()
        
    elif action == ReviewAction.FEEDBACK.value:
        print("\nFeedback Instructions:")
        print("Describe what's wrong with the query or how it should be improved:")
        data = input("Your feedback: ").strip()

    return {"action": action, "data": data}


async def main():
    """
    Initialize the MCP client and run the agent conversation loop.

    The MultiServerMCPClient allows connection to multiple MCP servers using a single client and config.
    """
    try:
        print_welcome()
        
        print("Initializing Prisma Query Agent...")
        
        client = MultiServerMCPClient(
            connections=mcp_config
        )
        
        # Get all tools (this should include both regular tools and resource access tools)
        tools = await client.get_tools()
        resource_list = await client.get_resources("prisma")
        if resource_list:
            # Assuming you want to get the first resource. You might need to loop through them.
            first_resource = resource_list[0]
            # You need to call the resource tool to get the data
            # schema_data = await client.run_resource(
            #     server_id="prisma",
            #     resource_id=first_resource.name
            # )
            print(f"Prisma schema data: {first_resource.metadata}")
     

        # Debug: Print available tools to see what we have
        print(f"Available tools: {[tool.name for tool in tools]}")
        
        graph = build_agent_graph(tools=tools, resources=resource_list or [], companyId=companyId)

        # pass a config with a thread_id to use memory
        graph_config = RunnableConfig(
            recursion_limit=25,
            configurable = {
                "thread_id": "1"
            }
        )
        
        print("Agent ready!\n")


        # Initial input
        graph_input = {
            "messages": [
                HumanMessage(content="Briefly introduce yourself and offer to help me.")
            ],
            "yolo_mode": yolo_mode
        }

        while True:
            # Run the graph until it interrupts
            print("CT Agent (Yours truly): ", end="")
            async for response in stream_graph_response(
                input=graph_input, 
                graph=graph, 
                config=graph_config
            ):
                print(response, end="", flush=True)

            # interrupt() throws internal exception(under the hood), LangGraph catches it and stores interrupt data in state
            thread_state = graph.get_state(config=graph_config)

            # Check if there are any interrupts
            while thread_state.interrupts:
                # you know in case there are multiple interrupts- haven't figured the parralel tool call yet!
                for interrupt in thread_state.interrupts:
                    # Handle human approval with enhanced interface
                    approval_result = await handle_human_approval(interrupt.value)
                    
                    if approval_result.get("action") == "exit":
                        print("\nSession terminated by user.")
                        return

                    # Resume the graph with human decision
                    print("\nPrismaGPT: ", end="")
                    async for response in stream_graph_response(
                        input=Command(resume=approval_result), 
                        graph=graph, 
                        config=graph_config
                    ):
                        print(response, end="", flush=True)

                    # Update thread state
                    thread_state = graph.get_state(config=graph_config)

            # Get next user input
            print("\n\nYou: ", end="")
            user_input = input().strip()
            
            if user_input.lower() in ["exit", "quit", "q"]:
                print("\nThank you for using Prisma Query Agent! Goodbye!\n")
                break
            

                
            elif not user_input:
                print("Please enter a query or type 'help' for assistance.")
                continue

            # Set up input for next iteration
            graph_input = PrismaAgentState(
                messages=[HumanMessage(content=user_input)],
                yolo_mode=yolo_mode
            )

    except KeyboardInterrupt:
        print("\n\nSession interrupted by user. Goodbye!")
    except Exception as e:
        print(f"\nError: {type(e).__name__}: {str(e)}")
        print("Please check your configuration and try again.")
        raise


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
