import os
import json
import re


with open(os.path.join(os.path.dirname(__file__), "mcp_config.json"), "r") as f:
    mcp_config = json.load(f)


def resolve_env_vars(config: dict):
    """Resolve environment variables in the MCP configuration"""
    for server, server_config in config.items():
        if "env" in server_config:
            for env_var in server_config["env"]:
                env_value = os.environ.get(env_var, "")
                config[server]["env"][env_var] = env_value
                # Set a default if the environment variable is not set
                if env_value == "" and env_var == "PRISMA_SCHEMA_PATH":
                    config[server]["env"][env_var] = "/Users/sj124894/playground/agents/schema.prisma"
                elif env_value == "" and env_var == "DATABASE_URL":
                    config[server]["env"][env_var] = "postgresql://localhost:5432/demo"
                    
        if "args" in server_config:
            for i, arg in enumerate(server_config["args"]):
                # Handle ${VAR} patterns anywhere in the string
                def replace_env_var(match):
                    env_var = match.group(1)
                    env_value = os.environ.get(env_var, "")
                    # Provide defaults for required variables
                    if env_value == "":
                        if env_var == "PRISMA_SCHEMA_PATH":
                            return "/Users/sj124894/playground/agents/schema.prisma"
                        elif env_var == "DATABASE_URL":
                            return "postgresql://localhost:5432/demo"  # Demo default
                        elif env_var == "WORKSPACE":
                            return os.path.dirname(__file__)  # Use current project directory
                        else:
                            raise ValueError(f"Environment variable {env_var} is not set")
                    return env_value

                # Replace all ${VAR} patterns in the string
                config[server]["args"][i] = re.sub(r'\$\{([^}]+)\}', replace_env_var, arg)
    return config


mcp_config = resolve_env_vars(mcp_config)
