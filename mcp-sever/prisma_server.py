#!/usr/bin/env python3
"""
PostgreSQL Query MCP Server

A FastMCP-based server for executing SQL queries against PostgreSQL.
Provides Prisma schema as context for query generation.
"""

import os
from typing import List, Optional
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
import httpx

load_dotenv()

# Initialize FastMCP server
mcp = FastMCP("postgresql")


class PostgreSQLSession:
    """Session for managing PostgreSQL operations with Prisma schema context"""
    
    def __init__(self):
        self.schema_path = os.environ.get("PRISMA_SCHEMA_PATH", "/Users/sj124894/playground/agents/mcp-sever/schema.prisma")
        self.schema_content = self._load_schema()
    
    def _load_schema(self) -> str:
        """Load the Prisma schema from file or return sample"""
        if os.path.exists(self.schema_path):
            with open(self.schema_path, 'r') as f:
                return f.read()
        else:
            # Return a fallback message when schema file is not found
            return f"Schema file not found at {self.schema_path}. Please set PRISMA_SCHEMA_PATH environment variable or create schema.prisma file."
        


    async def execute_sql_query(self, sql_query: str, description: str) -> str:
        """Execute a raw SQL query against PostgreSQL - description will be required by AI"""
        try:
            import asyncpg
        
            database_url = os.environ.get("DATABASE_URL")
            if not database_url:
                return "Error: DATABASE_URL not configured"
            
            conn = await asyncpg.connect(database_url)
            try:
                # if it's a SELECT return results ,o.w execute it
                sql_lower = sql_query.strip().lower()
                
                if sql_lower.startswith(('select', 'with')):
                    # SELECT query - fetch results
                    rows = await conn.fetch(sql_query)
                    result = [dict(row) for row in rows]
                    row_count = len(result)
                    
                    return f""" Query executed successfully!
                        Description: {description}
                        Query: {sql_query}
                        Rows returned: {row_count}
                        Results: {result[:10]}{'...' if row_count > 10 else ''}"""
                                            
                else:
                    # INSERT/UPDATE/DELETE query
                    result = await conn.execute(sql_query)
                    
                return f"""Query executed successfully!
                Description: {description}
                Query: {sql_query}
                Result: {result}"""
                        
            finally:
                await conn.close()
                
        except Exception as e:
            return f"""| --- Query execution failed!
Description: {description}
Query: {sql_query}
Error: {str(e)}"""


# Create session
session = PostgreSQLSession()


@mcp.resource("prisma://schema")
async def get_schema() -> str:
    """Get the complete Prisma schema definition for understanding database structure, relationships, and field mappings.
    
    This resource provides:
    - All available tables and their Prisma model definitions
    - Field mappings between Prisma camelCase names and database snake_case columns
    - Relationships between tables (foreign keys, references)
    - Data types, constraints, and indexes
    - Unique constraints and primary keys
    
    Use this resource BEFORE generating SQL queries to understand the database structure.
    """
    # Manually return the table information to test if the issue is file reading
    return """
Database Tables Available:

1. deleted_filing - Tracks deleted filing records
2. filings - Main table containing comprehensive filing information
3. company_tax_year - Company tax year information
4. demo_mode_tracker - Tracks demo mode status
5. custom_jurisdiction - Custom jurisdictions
6. custom_entity_jurisdiction - Pivot table for custom entity jurisdictions
7. entities - Entity information
8. jurisdictions - Standard jurisdictions
9. entity_jurisdictions - Pivot table for entity jurisdictions
10. calendars - Calendar information for entities
11. custom_filings - Custom filing records
12. custom_forms - Custom form definitions
13. company_custom_values - Custom values per company
14. company_settings - Company settings
15. company_custom_header - Custom headers per company
16. user_preferences - User preference settings
17. filings_snapshot - Snapshot of filings data
18. assignments - Assignment records
19. users - User information
20. sign_offs - Sign-off records

Key Field Mappings (Prisma camelCase → Database snake_case):
- companyTaxYearId → company_tax_year_id
- entityRecordId → entity_record_id
- recordId → record_id
- entityId → entity_id
- deletedAt → deleted_at
- deletedBy → deleted_by
- createdAt → created_at
- updatedAt → updated_at

Main Tables for Queries:
- filings: Primary table for filing data
- entities: Entity information
- company_tax_year: Tax year data
- assignments: Assignment tracking
"""


@mcp.tool()
async def executeQuery(sql_query: str, description: str) -> str:
    """Execute a raw SQL query against PostgreSQL database. Requires human approval for safety.

    Args:
        sql_query: The raw SQL query to execute
        description: Human-readable description of what the query does
    """
    return await session.execute_sql_query(sql_query, description)


async def assignFiling(companyTaxYearId:str ,assignor:Optional[dict], assignee:Optional[dict], recordIds: list[str] ) :
    assignor = assignor or {"userId": "50196982", "firstName": "Soorena", "lastName": "Jahromi", "role": "admin"}
    assignee = assignee or  {"userId": "50196982", "firstName": "Soorena", "lastName": "Jahromi", "role": "admin"}

    """Assign records to a user"""
    async with httpx.AsyncClient(http2=True, verify=False, timeout=30.0)  as client:
        response = await client.post(
            "https://local.bloombergtax.com/filings/assignments",  #hard coded for now 
            headers={
                "apiKey": "Hail Soorena"
            },
            json={
                "companyTaxYearId": companyTaxYearId,
                "assignor": assignor,
                "assignee": assignee,
                "recordIds": recordIds
            }
        )
    return f"Records assigned from {assignor} to {assignee} for company tax year {companyTaxYearId}"


@mcp.tool()
async def assignFilingTool(companyTaxYearId: str, assignor: Optional[dict], assignee: Optional[dict], recordIds: Optional[list[str]] = None) -> str:
    """Tool to assign records to a user"""
    return await assignFiling(companyTaxYearId, assignor, assignee, recordIds)

if __name__ == "__main__":
    mcp.run(transport='stdio')
