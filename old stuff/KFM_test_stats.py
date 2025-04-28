# -*- coding: utf-8 -*-
"""
Created on Sat Mar 29 19:38:28 2025

@author: jacob
"""

import sqlite3
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# User-defined parameters
DB_FILE = "kfm_data_90 test.db"  # Replace with your .db file path
TABLE_NAME = "sessions"      # Replace with your table name
COLUMN_NAME = "hold_time"    # Replace with the column you want to analyze
IDEAL_VALUE = 1.89              # Replace with your ideal value

# Step 1: Connect to the database and fetch data
conn = sqlite3.connect(DB_FILE)
query = f"SELECT {COLUMN_NAME} FROM {TABLE_NAME}"
df = pd.read_sql(query, conn)
conn.close()

# Convert column to NumPy array
data = df[COLUMN_NAME].dropna().to_numpy()

# Step 2: Calculate statistics
mean_value = np.mean(data)
median_value = np.median(data)
std_dev = np.std(data, ddof=1)  # Sample standard deviation
min_value = np.min(data)
max_value = np.max(data)
range_value = max_value - min_value
iqr = np.percentile(data, 75) - np.percentile(data, 25)

# Step 3: Compare with ideal value
mean_diff = mean_value - IDEAL_VALUE
median_diff = median_value - IDEAL_VALUE
z_scores = (data - IDEAL_VALUE) / std_dev
percentage_error = ((mean_value - IDEAL_VALUE) / IDEAL_VALUE) * 100

# Step 4: Print results
print(f"Mean: {mean_value}")
print(f"Median: {median_value}")
print(f"Standard Deviation: {std_dev}")
print(f"Min: {min_value}, Max: {max_value}, Range: {range_value}")
print(f"Interquartile Range: {iqr}")
print(f"Difference from Ideal (Mean): {mean_diff}")
print(f"Difference from Ideal (Median): {median_diff}")
print(f"Percentage Error: {percentage_error:.2f}%")

# Step 5: Plot histogram
plt.figure(figsize=(8, 5))
plt.hist(data, bins=50, alpha=0.7, color="blue", label="Data")
plt.axvline(IDEAL_VALUE, color="red", linestyle="dashed", linewidth=2, label="Ideal Value")
plt.xlabel("Value")
plt.ylabel("Frequency")
plt.title("Data Distribution vs Ideal Value")
plt.legend()
plt.show()
