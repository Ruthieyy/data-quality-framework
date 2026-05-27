import pandas as pd
import great_expectations as ge

# 从S3读取真实数据
df = ge.from_pandas(pd.read_csv("s3://yu-data-lake-test/test.csv"))

print(f"读取到 {len(df)} 行数据")
print(df.head())
print()

# 检查1：缺失值
result1 = df.expect_column_values_to_not_be_null('name')
if not result1['success']:
    bad_rows = df[df['name'].isnull()].index.tolist()
    print(f"❌ 缺失值 — 问题行: {bad_rows}")
else:
    print(f"✅ name列无缺失值")

# 检查2：age范围
result2 = df.expect_column_values_to_be_between('age', 0, 120)
if not result2['success']:
    bad_rows = df[(df['age'] < 0) | (df['age'] > 120)].index.tolist()
    print(f"❌ age异常 — 问题行: {bad_rows}")
else:
    print(f"✅ age范围正常")
