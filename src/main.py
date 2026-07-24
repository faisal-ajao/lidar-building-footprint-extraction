import numpy as np
import pandas as pd
import open3d as o3d
import matplotlib.pyplot as plt
import yaml

# ======================================================
# Load Configuration
# ======================================================
with open("config.yaml", "r") as file:
    config = yaml.safe_load(file)

pc_dataset = config["datasets"]["point_cloud"]

voxel_size = config["voxel"]["size"]
downsample_step = config["preprocessing"]["downsample_step"]

poi_radius = config["poi"]["radius"]
building_class = config["classification"]["building"]

filtered_pcd_output = config["output"]["filtered_point_cloud"]
footprint_output =  config["output"]["footprint_image"]


# ======================================================
# Initial Profiling
# ======================================================
pcd_df = pd.read_csv(pc_dataset, delimiter=";")

required_columns = ["X", "Y", "Z", "R", "G", "B", "Classification"]

missing = [col for col in required_columns if col not in pcd_df.columns]

if missing:
    raise ValueError(f"Missing required columns: {missing}")

print("Columns:")
print(list(pcd_df.columns))

print("\nShape:")
print(pcd_df.shape)

print("\nStatistics:")
print(pcd_df.describe())


# ======================================================
# Downsample the Point Cloud
# ======================================================
pcd_subsampled = pcd_df.iloc[::downsample_step].copy()


# ======================================================
# Convert to Open3D Point Cloud
# ======================================================
pcd_o3d = o3d.geometry.PointCloud()

pcd_o3d.points = o3d.utility.Vector3dVector(
    pcd_subsampled[["X", "Y", "Z"]].to_numpy()
)

pcd_o3d.colors = o3d.utility.Vector3dVector(
    pcd_subsampled[["R", "G", "B"]].to_numpy() / 255
)


# ======================================================
# Center the Point Cloud
# ======================================================
pcd_center = pcd_o3d.get_center()
pcd_o3d.translate(-pcd_center)


# ======================================================
# Select Point of Interest (POI)
# ======================================================
vis = o3d.visualization.VisualizerWithEditing()
vis.create_window()

vis.add_geometry(pcd_o3d)

print("Shift + Left Click to select one point.")
print("Press Q when finished.")

vis.run()
vis.destroy_window()

picked_indices = vis.get_picked_points()

if len(picked_indices) != 1:
    raise ValueError("Please select exactly one point.")

point_index = picked_indices[0]


# ======================================================
# Highlight Buildings Only
# ======================================================
colors = np.zeros((len(pcd_subsampled), 3))

colors[pcd_subsampled["Classification"] == building_class] = [1, 0, 0]

pcd_o3d.colors = o3d.utility.Vector3dVector(colors)

o3d.visualization.draw_geometries(
    [pcd_o3d],
    window_name="Buildings Highlighted"
)


# ======================================================
# KD-Tree Radius Search
# ======================================================
print("Processing...")
pcd_tree = o3d.geometry.KDTreeFlann(pcd_o3d)

poi = np.asarray(pcd_o3d.points)[point_index]

_, idx, _ = pcd_tree.search_radius_vector_3d(
    poi,
    poi_radius
)

pcd_selection = pcd_o3d.select_by_index(idx)


# ======================================================
# Visualize Selected Region
# ======================================================
poi_sphere = o3d.geometry.TriangleMesh.create_sphere(radius=5)
poi_sphere.translate(poi)
poi_sphere.paint_uniform_color([0, 1, 0])

o3d.visualization.draw_geometries(
    [pcd_selection, poi_sphere],
    window_name="Selected Neighbourhood"
)


# ======================================================
# Create Voxel Grid
# ======================================================
voxel_grid = o3d.geometry.VoxelGrid.create_from_point_cloud(
    pcd_selection,
    voxel_size
)

o3d.visualization.draw_geometries(
    [voxel_grid],
    window_name="Voxel Grid"
)


# ======================================================
# Extract Voxel Information
# ======================================================
if len(voxel_grid.get_voxels()) == 0:
    raise ValueError(
        "No voxels were generated. Try increasing the search radius or decreasing the voxel size."
    )
idx_voxels = [v.grid_index for v in voxel_grid.get_voxels()]
color_voxels = [v.color for v in voxel_grid.get_voxels()]

bound_voxels = [
    np.min(idx_voxels, axis=0),
    np.max(idx_voxels, axis=0)
]

print("\nVoxel Bounds:")
print(bound_voxels)

print(f"Number of Voxels: {len(idx_voxels)}")


# ======================================================
# Built Coverage Extraction
# ======================================================
max_voxel = {}
max_color = {}

for idx, voxel in enumerate(idx_voxels):

    key = (voxel[0], voxel[1])

    if key in max_voxel:

        if voxel[2] > max_voxel[key]:
            max_voxel[key] = voxel[2]
            max_color[key] = color_voxels[idx]

    else:
        max_voxel[key] = voxel[2]
        max_color[key] = color_voxels[idx]


count_building_coverage = 0
count_non_building = 0

for color in max_color.values():

    if np.all(color == 0):
        count_non_building += 1
    else:
        count_building_coverage += 1

cell_area = voxel_size ** 2
building_area = count_building_coverage * cell_area
non_building_area = count_non_building * cell_area
total_area = building_area + non_building_area
building_ratio = (
    building_area / total_area
    if total_area > 0
    else 0
)

print(f"\nCoverage of Buildings : {building_area} m²")
print(f"Coverage of the Rest  : {non_building_area} m²")
print(f"Building Ratio        : {building_ratio:.3f}")


# ======================================================
# Generate Footprint Image
# ======================================================
xy_values = np.array(list(max_voxel.keys()), dtype=int)

min_x = np.min(xy_values[:, 0])
max_x = np.max(xy_values[:, 0])

min_y = np.min(xy_values[:, 1])
max_y = np.max(xy_values[:, 1])

img_width = max_x - min_x + 1
img_height = max_y - min_y + 1

footprint = np.zeros((img_height, img_width), dtype=np.uint8)

for (x, y), color in max_color.items():
    pixel_x = x - min_x
    pixel_y = max_y - y

    if np.all(color == 0):
        footprint[pixel_y, pixel_x] = 0
    else:
        footprint[pixel_y, pixel_x] = 255

plt.imshow(footprint, cmap="gray", interpolation="nearest")
plt.axis("off")
plt.tight_layout(pad=0)
plt.savefig(footprint_output, dpi=300, bbox_inches="tight")
plt.show()

# ======================================================
# Export Results
# ======================================================
o3d.io.write_point_cloud(
    filtered_pcd_output,
    pcd_selection,
    write_ascii=False,
    compressed=False,
    print_progress=False
)

print(f"\nFiltered point cloud saved to:\n{filtered_pcd_output}")