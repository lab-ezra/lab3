import io
import json
import math
import os
import subprocess
import tempfile
import zipfile
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
from PIL import Image

# ------------------------------------------------------------
# PERPUSTAKAAN SOKONGAN GIS & CAD
# ------------------------------------------------------------
try:
    import pyproj

    PYPROJ_AVAILABLE = True
except ImportError:
    PYPROJ_AVAILABLE = False

try:
    import folium
    from streamlit_folium import st_folium

    FOLIUM_AVAILABLE = True
except ImportError:
    FOLIUM_AVAILABLE = False

try:
    import ezdxf

    EZDXF_AVAILABLE = True
except ImportError:
    EZDXF_AVAILABLE = False

try:
    import geopandas as gpd
    from shapely.geometry import Point, Polygon

    GEOPANDAS_AVAILABLE = True
except ImportError:
    GEOPANDAS_AVAILABLE = False


# ============================================================
# 1. TETAPAN APLIKASI & SESSION STATE
# ============================================================
st.set_page_config(
    page_title="Sistem GIS & Pelan Ukur Lot", page_icon="🗺️", layout="wide"
)

if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False

if "proses_diklik" not in st.session_state:
    st.session_state["proses_diklik"] = False


# ============================================================
# 2. FUNGSI KIRAAN GEOMETRI & BEARING
# ============================================================
def kira_poligon(x, y):
    """Kira luas, centroid, dan perimeter poligon."""
    n = len(x)
    jumlah = 0.0
    cx, cy = 0.0, 0.0
    perimeter = 0.0

    for i in range(n):
        j = (i + 1) % n
        silang = (x[i] * y[j]) - (x[j] * y[i])
        jumlah += silang
        cx += (x[i] + x[j]) * silang
        cy += (y[i] + y[j]) * silang
        perimeter += math.hypot(x[j] - x[i], y[j] - y[i])

    signed_area = jumlah / 2.0
    luas = abs(signed_area)

    if abs(signed_area) > 1e-12:
        cx /= 6.0 * signed_area
        cy /= 6.0 * signed_area
    else:
        cx = sum(x) / n
        cy = sum(y) / n

    return luas, cx, cy, perimeter


def format_bearing(angle_deg):
    """Tukar bearing perpuluhan kepada DMS."""
    angle_deg %= 360.0
    d = int(angle_deg)
    min_float = (angle_deg - d) * 60.0
    m = int(min_float)
    s = round((min_float - m) * 60.0, 1)

    if s >= 60.0:
        s = 0.0
        m += 1
    if m >= 60:
        m = 0
        d = (d + 1) % 360

    return f"{d}°{m:02d}'{s:04.1f}\""


def kira_bearing_jarak(x, y, stn_ids):
    """Kira bearing azimut dan jarak antara stesen."""
    data = []
    n = len(x)

    for i in range(n):
        j = (i + 1) % n
        de = x[j] - x[i]
        dn = y[j] - y[i]

        jarak = math.hypot(de, dn)
        bearing_deg = (math.degrees(math.atan2(de, dn)) + 360.0) % 360.0

        data.append({
            "Dari Stesen": stn_ids[i],
            "Ke Stesen": stn_ids[j],
            "Bearing": format_bearing(bearing_deg),
            "Jarak (m)": round(jarak, 3),
            "Easting1": x[i],
            "Northing1": y[i],
            "Easting2": x[j],
            "Northing2": y[j],
            "Mid_X": (x[i] + x[j]) / 2.0,
            "Mid_Y": (y[i] + y[j]) / 2.0,
        })

    return pd.DataFrame(data)


def tukar_koordinat_ke_wgs84(x_list, y_list, source_epsg):
    """Penukaran koordinat tempatan kepada WGS84."""
    if not PYPROJ_AVAILABLE or source_epsg == "EPSG:4326":
        return list(zip(y_list, x_list))

    try:
        transformer = pyproj.Transformer.from_crs(
            source_epsg, "EPSG:4326", always_xy=True
        )
        lon_lat = [
            transformer.transform(x, y) for x, y in zip(x_list, y_list)
        ]
        return [(lat, lon) for lon, lat in lon_lat]
    except Exception as e:
        st.warning(f"Gagal menukar CRS ({source_epsg}) ke WGS84: {e}")
        return []


# ============================================================
# 3. FUNGSI EKSPORT DATA (DXF & DWG INCLUDED)
# ============================================================
def eksport_txt(df_bj, luas, perimeter):
    txt_out = "=== LAPORAN UKURAN LOT SEMPADAN ===\n"
    txt_out += f"Luas Lot     : {luas:,.2f} m²\n"
    txt_out += f"Perimeter    : {perimeter:,.3f} m\n\n"
    txt_out += "JADUAL BEARING & JARAK:\n"
    txt_out += df_bj[
        ["Dari Stesen", "Ke Stesen", "Bearing", "Jarak (m)"]
    ].to_string(index=False)
    return txt_out.encode("utf-8")


def eksport_geojson(x, y, stn_ids, epsg_code):
    coords = [[x[i], y[i]] for i in range(len(x))] + [[x[0], y[0]]]
    geojson = {
        "type": "FeatureCollection",
        "crs": {
            "type": "name",
            "properties": {"name": f"urn:ogc:def:crs:OGC:1.3:{epsg_code}"},
        },
        "features": [
            {
                "type": "Feature",
                "properties": {"Name": "Lot Sempadan"},
                "geometry": {"type": "Polygon", "coordinates": [coords]},
            }
        ],
    }
    for i in range(len(x)):
        geojson["features"].append({
            "type": "Feature",
            "properties": {
                "STN_ID": stn_ids[i],
                "Easting": x[i],
                "Northing": y[i],
            },
            "geometry": {"type": "Point", "coordinates": [x[i], y[i]]},
        })
    return json.dumps(geojson, indent=2).encode("utf-8")


def eksport_kml(lat_lon_pts, stn_ids, luas):
    kml = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Pelan Sempadan Lot</name>
    <Placemark>
      <name>Lot Sempadan</name>
      <description>Luas: {:.2f} m2</description>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>
""".format(luas)
    for lat, lon in lat_lon_pts:
        kml += f"              {lon},{lat},0\n"
    if lat_lon_pts:
        kml += f"              {lat_lon_pts[0][1]},{lat_lon_pts[0][0]},0\n"

    kml += """            </coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>
"""
    for i, (lat, lon) in enumerate(lat_lon_pts):
        kml += f"""    <Placemark>
      <name>STN {stn_ids[i]}</name>
      <Point>
        <coordinates>{lon},{lat},0</coordinates>
      </Point>
    </Placemark>
"""
    kml += """  </Document>
</kml>"""
    return kml.encode("utf-8")


def eksport_dxf_dan_dwg(x, y, stn_ids, df_bj):
    """Jana fail DXF dan tukar ke DWG secara automatik (jika ODA Converter ada)."""
    if not EZDXF_AVAILABLE:
        # DXF fallback jika tiada ezdxf
        dxf = "0\nSECTION\n2\nENTITIES\n"
        for i in range(len(x)):
            j = (i + 1) % len(x)
            dxf += f"0\nLINE\n8\nSEMPADAN\n10\n{x[i]}\n20\n{y[i]}\n11\n{x[j]}\n21\n{y[j]}\n"
        dxf += "0\nENDSEC\n0\nEOF\n"
        return dxf.encode("utf-8"), None

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()

    # 1. Polyline Sempadan
    pts = [(x[i], y[i]) for i in range(len(x))] + [(x[0], y[0])]
    msp.add_lwpolyline(pts, dxfattribs={"layer": "SEMPADAN", "color": 5})

    # 2. Teks Stesen
    for i in range(len(x)):
        msp.add_point((x[i], y[i]), dxfattribs={"layer": "STESEN"})
        msp.add_text(
            f"STN {stn_ids[i]}", dxfattribs={"height": 0.5, "layer": "STESEN"}
        ).set_placement((x[i] + 0.3, y[i] + 0.3))

    # 3. Teks Bearing & Jarak
    for _, row in df_bj.iterrows():
        txt = f"{row['Bearing']} | {row['Jarak (m)']:.3f}m"
        msp.add_text(
            txt, dxfattribs={"height": 0.4, "layer": "BEARING_JARAK"}
        ).set_placement((row["Mid_X"], row["Mid_Y"]))

    with tempfile.TemporaryDirectory() as tmpdir:
        dxf_path = os.path.join(tmpdir, "pelan_lot.dxf")
        doc.saveas(dxf_path)

        with open(dxf_path, "rb") as f:
            dxf_bytes = f.read()

        dwg_bytes = None
        # Penukaran automatik ke DWG melalui ODA Converter jika ada dipasang
        oda_path = r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe"
        if os.path.exists(oda_path):
            out_dir = os.path.join(tmpdir, "output")
            os.makedirs(out_dir, exist_ok=True)
            cmd = [
                oda_path,
                tmpdir,
                out_dir,
                "ACAD2010",
                "DWG",
                "0",
                "1",
                "pelan_lot.dxf",
            ]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            dwg_file = os.path.join(out_dir, "pelan_lot.dwg")
            if os.path.exists(dwg_file):
                with open(dwg_file, "rb") as f_dwg:
                    dwg_bytes = f_dwg.read()

    return dxf_bytes, dwg_bytes


def eksport_shapefile_zip(x, y, stn_ids, epsg_code):
    if not GEOPANDAS_AVAILABLE:
        return None

    poly_geom = Polygon([(x[i], y[i]) for i in range(len(x))])
    gdf = gpd.GeoDataFrame(
        [{"geometry": poly_geom, "Name": "Lot Sempadan"}], crs=epsg_code
    )

    pts_geom = [Point(x[i], y[i]) for i in range(len(x))]
    gdf_pts = gpd.GeoDataFrame(
        {"STN_ID": stn_ids, "geometry": pts_geom}, crs=epsg_code
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        poly_path = f"{tmpdir}/lot_sempadan.shp"
        pts_path = f"{tmpdir}/stesen.shp"
        gdf.to_file(poly_path)
        gdf_pts.to_file(pts_path)

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for ext in ["shp", "shx", "dbf", "prj"]:
                zip_file.write(
                    f"{tmpdir}/lot_sempadan.{ext}",
                    arcname=f"lot_sempadan.{ext}",
                )
                zip_file.write(
                    f"{tmpdir}/stesen.{ext}", arcname=f"stesen.{ext}"
                )
        return zip_buffer.getvalue()


# ============================================================
# 4. LOGO & LOG IN
# ============================================================
try:
    logo = Image.open("Poli_Logo.png")
    st.image(logo, width=220)
except FileNotFoundError:
    pass

if not st.session_state["logged_in"]:
    st.title("Sistem Maklumat Geografi (GIS)")
    st.subheader("Log Masuk Pengguna")

    username = st.text_input("Nama Pengguna")
    password = st.text_input("Kata Laluan", type="password")

    if st.button("Log Masuk", type="primary"):
        if username == "admin" and password == "rahsia123":
            st.session_state["logged_in"] = True
            st.rerun()
        elif not username or not password:
            st.warning("Sila masukkan nama pengguna dan kata laluan.")
        else:
            st.error("Nama pengguna atau kata laluan salah.")

# ============================================================
# 5. ANTARAMUKA UTAMA & GIS
# ============================================================
else:
    st.sidebar.title("Sistem GIS Ukur")
    st.sidebar.success("Status: Log Masuk (admin)")

    if st.sidebar.button("Log Keluar"):
        st.session_state["logged_in"] = False
        st.session_state["proses_diklik"] = False
        st.rerun()

    st.title("🗺️ Pemprosesan Data Ukur & Layer GIS")
    st.caption(
        "Kawalan Layer Modular, Sistem Projeksi CRS, Peta Interaktif & Eksport"
        " CAD/GIS."
    )

    uploaded_file = st.file_uploader(
        "Muat naik fail CSV koordinat", type=["csv"]
    )

    if uploaded_file is not None:
        try:
            df = pd.read_csv(uploaded_file)
            df.columns = df.columns.astype(str).str.strip()

            if df.empty or len(df.columns) < 2:
                st.error(
                    "Fail CSV tidak sah atau mempunyai kurang daripada 2"
                    " kolom."
                )
                st.stop()

            st.dataframe(df, use_container_width=True, height=180)

            c1, c2, c3, c4 = st.columns(4)
            with c1:
                x_col = st.selectbox("X / Easting", df.columns, index=0)
            with c2:
                y_col = st.selectbox(
                    "Y / Northing",
                    df.columns,
                    index=1 if len(df.columns) > 1 else 0,
                )
            with c3:
                stn_col = st.selectbox(
                    "ID Stesen",
                    ["Nombor Baris (Auto STN)"] + list(df.columns),
                )
            with c4:
                crs_option = st.selectbox(
                    "Sistem Koordinat (CRS)",
                    [
                        "EPSG:3375 (GDM2000 Peninsular RSO)",
                        "EPSG:3168 (Kertau RSO Malaya)",
                        "EPSG:3376 (GDM2000 East Malaysia BRSO)",
                        "EPSG:4326 (WGS84 Lat/Lon)",
                        "Custom EPSG",
                    ],
                )

            if crs_option == "Custom EPSG":
                selected_epsg = st.text_input(
                    "Masukkan kod EPSG (contoh: EPSG:3857)", value="EPSG:3375"
                )
            else:
                selected_epsg = crs_option.split(" ")[0]

            if st.button(
                "Jana Pelan & Layer GIS",
                type="primary",
                use_container_width=True,
            ):
                st.session_state["proses_diklik"] = True

            if st.session_state["proses_diklik"]:
                work_df = pd.DataFrame({
                    "X": pd.to_numeric(df[x_col], errors="coerce"),
                    "Y": pd.to_numeric(df[y_col], errors="coerce"),
                })
                if stn_col != "Nombor Baris (Auto STN)":
                    work_df["STN"] = df[stn_col].astype(str)

                work_df = work_df.dropna(subset=["X", "Y"]).reset_index(
                    drop=True
                )

                if len(work_df) < 3:
                    st.error(
                        "Sekurang-kurangnya 3 titik koordinat yang sah"
                        " diperlukan."
                    )
                    st.stop()

                x = work_df["X"].tolist()
                y = work_df["Y"].tolist()

                if stn_col == "Nombor Baris (Auto STN)":
                    stn_ids = [f"STN {i + 1}" for i in range(len(work_df))]
                else:
                    stn_ids = work_df["STN"].tolist()

                luas, cx, cy, perimeter = kira_poligon(x, y)
                df_bj = kira_bearing_jarak(x, y, stn_ids)

                # ------------------------------------------------
                # KAWALAN LAYER MODULAR
                # ------------------------------------------------
                st.write("---")
                st.subheader("🎛️ Kawalan Layer Modul GIS")
                l1, l2, l3, l4, l5, l6 = st.columns(6)
                with l1:
                    show_polygon = st.checkbox(
                        "Layer Lot/Polygon", value=True
                    )
                with l2:
                    show_pts = st.checkbox("Layer Titik Stesen", value=True)
                with l3:
                    show_stn_id = st.checkbox("Layer ID Stesen", value=True)
                with l4:
                    show_coords = st.checkbox(
                        "Layer Koordinat E/N", value=False
                    )
                with l5:
                    show_bj = st.checkbox("Layer Bearing & Jarak", value=True)
                with l6:
                    show_area = st.checkbox("Layer Label Lot + Luas", value=True)

                # ------------------------------------------------
                # 1. PAPARAN GRAFIK CAD (DENGAN LABEL DI LUAR LOT)
                # ------------------------------------------------
                fig, ax = plt.subplots(figsize=(9, 7))
                x_plot = x + [x[0]]
                y_plot = y + [y[0]]

                if show_polygon:
                    ax.fill(x_plot, y_plot, alpha=0.15, color="#1f77b4")
                    ax.plot(
                        x_plot,
                        y_plot,
                        color="#1f77b4",
                        linewidth=2,
                        label="Garisan Sempadan",
                    )

                for i in range(len(x)):
                    if show_pts:
                        ax.scatter(x[i], y[i], color="red", s=40, zorder=5)

                    label_str = ""
                    if show_stn_id:
                        label_str += f"{stn_ids[i]}"
                    if show_coords:
                        label_str += f"\nE:{x[i]:,.2f}\nN:{y[i]:,.2f}"

                    if label_str.strip():
                        ax.annotate(
                            label_str.strip(),
                            (x[i], y[i]),
                            xytext=(5, 5),
                            textcoords="offset points",
                            fontsize=8,
                            fontweight="bold",
                        )

                # LAYER BEARING & JARAK (DITOLAK KE LUAR AUTOMATIK)
                if show_bj:
                    fig.canvas.draw()
                    for _, row in df_bj.iterrows():
                        p1 = ax.transData.transform(
                            (row["Easting1"], row["Northing1"])
                        )
                        p2 = ax.transData.transform(
                            (row["Easting2"], row["Northing2"])
                        )
                        dx_pix, dy_pix = p2[0] - p1[0], p2[1] - p1[1]

                        angle_screen = math.degrees(math.atan2(dy_pix, dx_pix))
                        if angle_screen > 90:
                            angle_screen -= 180
                        elif angle_screen < -90:
                            angle_screen += 180

                        nx = -(row["Northing2"] - row["Northing1"])
                        ny = row["Easting2"] - row["Easting1"]
                        length = math.hypot(nx, ny)
                        if length != 0:
                            nx /= length
                            ny /= length

                        vx = row["Mid_X"] - cx
                        vy = row["Mid_Y"] - cy
                        if (nx * vx + ny * vy) < 0:
                            nx, ny = -nx, -ny

                        off_x = nx * 14
                        off_y = ny * 14
                        txt = f'{row["Bearing"]}  |  {row["Jarak (m)"]:.3f} m'

                        ax.annotate(
                            txt,
                            xy=(row["Mid_X"], row["Mid_Y"]),
                            xytext=(off_x, off_y),
                            textcoords="offset points",
                            fontsize=8,
                            fontweight="bold",
                            color="black",
                            rotation=angle_screen,
                            rotation_mode="anchor",
                            ha="center",
                            va="center",
                            bbox=dict(
                                boxstyle="round,pad=0.3",
                                facecolor="white",
                                edgecolor="#666666",
                                linewidth=0.8,
                                alpha=0.95,
                            ),
                            zorder=5,
                        )

                if show_area:
                    ax.text(
                        cx,
                        cy,
                        f"LOT SEMPADAN\n{luas:,.2f} m²",
                        fontsize=10,
                        fontweight="bold",
                        color="darkgreen",
                        ha="center",
                        va="center",
                        bbox=dict(
                            facecolor="white",
                            alpha=0.9,
                            edgecolor="darkgreen",
                            pad=2.0,
                        ),
                    )

                ax.set_title(
                    f"Pelan Sempadan Lot ({selected_epsg})", fontsize=12
                )
                ax.set_xlabel("Easting (X)")
                ax.set_ylabel("Northing (Y)")
                ax.set_aspect("equal", adjustable="datalim")
                ax.grid(True, linestyle="--", alpha=0.35)

                st.pyplot(fig)
                plt.close(fig)

                # ------------------------------------------------
                # 2. PETA INTERAKTIF
                # ------------------------------------------------
                st.subheader("🌍 Peta Interaktif GIS (Basemaps)")
                lat_lon_pts = tukar_koordinat_ke_wgs84(x, y, selected_epsg)

                if FOLIUM_AVAILABLE and lat_lon_pts:
                    avg_lat = sum(p[0] for p in lat_lon_pts) / len(lat_lon_pts)
                    avg_lon = sum(p[1] for p in lat_lon_pts) / len(lat_lon_pts)
                    m = folium.Map(location=[avg_lat, avg_lon], zoom_start=18)

                    folium.TileLayer("OpenStreetMap").add_to(m)
                    folium.TileLayer(
                        tiles=(
                            "https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}"
                        ),
                        attr="Google Satellite",
                        name="Google Satellite",
                    ).add_to(m)

                    if show_polygon:
                        folium.Polygon(
                            locations=lat_lon_pts,
                            color="blue",
                            fill=True,
                            fill_color="blue",
                            fill_opacity=0.2,
                            popup=f"Luas Lot: {luas:,.2f} m²",
                        ).add_to(m)

                    for i, (lat, lon) in enumerate(lat_lon_pts):
                        popup_txt = f"<b>{stn_ids[i]}</b>"
                        if show_coords:
                            popup_txt += (
                                f"<br>E: {x[i]:,.2f}<br>N: {y[i]:,.2f}"
                            )

                        if show_pts:
                            folium.CircleMarker(
                                location=[lat, lon],
                                radius=5,
                                color="red",
                                fill=True,
                                popup=popup_txt,
                                tooltip=stn_ids[i] if show_stn_id else None,
                            ).add_to(m)

                    folium.LayerControl().add_to(m)
                    st_folium(m, width=900, height=500)

                # ------------------------------------------------
                # 3. MAKLUMAT & JADUAL
                # ------------------------------------------------
                m1, m2 = st.columns(2)
                with m1:
                    st.metric("Jumlah Luas Lot", f"{luas:,.2f} m²")
                with m2:
                    st.metric("Jumlah Perimeter", f"{perimeter:,.3f} m")

                st.subheader("📋 Jadual Ukuran Bearing & Jarak")
                st.dataframe(
                    df_bj[
                        ["Dari Stesen", "Ke Stesen", "Bearing", "Jarak (m)"]
                    ],
                    use_container_width=True,
                )

                # ------------------------------------------------
                # 4. EKSPORT MULTI-FORMAT (TERMASUK DWG & DXF)
                # ------------------------------------------------
                st.write("---")
                st.subheader(
                    "📥 Muat Turun Data & Format CAD / GIS (DWG, DXF, SHP,"
                    " GeoJSON)"
                )

                dxf_bytes, dwg_bytes = eksport_dxf_dan_dwg(x, y, stn_ids, df_bj)

                ex1, ex2, ex3, ex4, ex5, ex6 = st.columns(6)

                with ex1:
                    st.download_button(
                        "📊 CSV",
                        df_bj.to_csv(index=False).encode("utf-8"),
                        "bearing_jarak.csv",
                        "text/csv",
                    )
                with ex2:
                    st.download_button(
                        "📄 TXT",
                        eksport_txt(df_bj, luas, perimeter),
                        "laporan_ukuran.txt",
                        "text/plain",
                    )
                with ex3:
                    st.download_button(
                        "🌐 GeoJSON",
                        eksport_geojson(x, y, stn_ids, selected_epsg),
                        "lot_sempadan.geojson",
                        "application/json",
                    )
                with ex4:
                    st.download_button(
                        "🗺️ KML",
                        eksport_kml(lat_lon_pts, stn_ids, luas),
                        "lot_sempadan.kml",
                        "application/vnd.google-earth.kml+xml",
                    )
                with ex5:
                    st.download_button(
                        "✏️ DXF (AutoCAD)",
                        dxf_bytes,
                        "pelan_lot.dxf",
                        "application/dxf",
                    )
                with ex6:
                    if dwg_bytes:
                        st.download_button(
                            "🏗️ DWG (AutoCAD)",
                            dwg_bytes,
                            "pelan_lot.dwg",
                            "application/acad",
                            type="primary",
                        )
                    else:
                        st.download_button(
                            "✏️ DWG via DXF",
                            dxf_bytes,
                            "pelan_lot.dwg",
                            "application/dwg",
                        )

                shp_zip = eksport_shapefile_zip(x, y, stn_ids, selected_epsg)
                if shp_zip:
                    st.download_button(
                        "📦 Shapefile ZIP",
                        shp_zip,
                        "shapefile_lot.zip",
                        "application/zip",
                    )

        except Exception as e:
            st.error(f"Ralat membaca atau memproses fail CSV: {e}")