from langchain_community.tools.pubmed.tool import PubmedQueryRun


tool = PubmedQueryRun()

query = "CRISPR gene editing"

print("Tool name:")
print(tool.name)

print("\nResult:")
result = tool.invoke(query)

print(result[:2000])
