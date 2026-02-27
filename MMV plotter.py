import matplotlib.pyplot as plt
import numpy as np

def quick_plot_func(func, x_start, x_end, num_points=100, title="Quick Plot", xlabel="X-axis", ylabel="Y-axis", annotation=None):
    """
    quick_plot_func: Creates a clear, annotated line plot of y = func(x) using matplotlib.

    Parameters:
    - func (callable): Function of x that returns y values.
    - x_start (float): Start value of x range.
    - x_end (float): End value of x range.
    - num_points (int): Number of points to sample between x_start and x_end.
    - title (str): Title of the plot.
    - xlabel (str): Label for the x-axis.
    - ylabel (str): Label for the y-axis.
    - annotation (dict or None): If provided, annotate a point.
        Should be dict with keys:
        {
            'x': float,      # x-coordinate to annotate
            'text': str,     # annotation text
            'xytext': tuple  # text position offset (dx, dy)
        }
    """
    x = np.linspace(x_start, x_end, num_points)
    y = func(x)

    plt.figure()
    plt.plot(x, y, marker='o')
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.grid(True)

    # Add annotation if provided
    if annotation:
        x0 = annotation['x']
        y0 = func(x0)
        plt.annotate(annotation['text'],
                     xy=(x0, y0),
                     xytext=annotation.get('xytext', (10, 10)),
                     textcoords='offset points',
                     arrowprops=dict(arrowstyle='->'))

    plt.tight_layout()
    plt.show()


# Example function definitions
def sine_wave(x):
    return np.sin(x)

def parabola(x):
    return x**2 - 4*x + 3

def medicine(x):
    return -0.00981*np.cos(22.4*x)+(-7.67/22.4)*np.sin(22.4*x)-0.00981


quick_plot_func(
   medicine,
    x_start=0,
    x_end=1,
    num_points=500,
    title="Medicine Example",
    xlabel="X",
    ylabel="f(x)",
    annotation={'x': 2, 'text': "Vertex", 'xytext': (0, -30)}
)
