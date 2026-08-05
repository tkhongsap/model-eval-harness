import pandas as pd

dfs = pd.read_html('GROUP4_180427.XLS')

for i, table in enumerate(dfs):
    print(f"Table {i}: {table.shape}")

df = dfs[3]
df.columns = df.iloc[0]
df = df[1:].reset_index(drop=True)

print(df.head(20))
print("="*50)
print(df.columns)
print("="*50)
print(len(df))
print("="*50)
# print(df.iloc[54, :])  