import numpy as np
import matplotlib.pyplot as plt

# parameters
L = 1e-3
D = 1e-10
Nx = 100
Nt = 1000
T = 100

# Discretization
dx = L / Nx
dt = T / Nt
x = np.linspace(0, L, Nx)

# stability condition
alpha = D * dt / dx**2
if alpha > 0.5:
    raise ValueError("Stability condition violated: alpha must be <= 0.5")

# Initial condition
C = np.zeros(Nx)
C[0] = 1.0

# store
concentration_over_time = [C.copy()]

# time stepping
for n in range(Nt):
    C_new = C.copy()
    for i in range(1, Nx - 1):
        C_new[i] = C[i] + alpha * (C[i + 1] - 2 * C[i] + C[i - 1])

    C = C_new
    concentration_over_time.append(C.copy())

#plotting final conc profile
plt.plot(x * 1e3, C, label=f'Time = {Nt * dt:.2f} s')
plt.xlabel("pos in gut wall(mm)")
plt.ylabel("concentration (mol/m^3)")
plt.title("Concentration Profile in Gut Wall Over Time")
plt.grid()
plt.show()