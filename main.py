import re
import requests
import pandas as pd
import streamlit as st
import plotly.express as px

st.set_page_config(page_title="전국 고령화 지도", layout="wide")
st.title("🗺️ 전국 고령화 지도")
st.caption("시군구별 65세 이상 인구 비율 (행정안전부 주민등록 인구)")

POP_URL = "https://raw.githubusercontent.com/greatsong/modudata/main/data/population_yearly.csv.gz"
GEO_URL = "https://raw.githubusercontent.com/greatsong/modudata/main/data/boundaries/sigungu_kr.geojson"

@st.cache_data(show_spinner="인구 데이터를 불러오는 중입니다...")
def load_population():
    # '코드' 열은 앞자리 0이 사라지지 않게 글자로 읽습니다
    return pd.read_csv(POP_URL, dtype={"코드": str})

@st.cache_data(show_spinner="지도 경계를 불러오는 중입니다...")
def load_geojson():
    return requests.get(GEO_URL, timeout=30).json()

df = load_population()
geojson = load_geojson()

# 1. 가장 최신 연도만 사용
latest_year = int(df["연도"].max())
df = df[df["연도"] == latest_year].copy()

# 2. '계_'로 시작하는 나이 열만 (남_·여_ 열까지 더하면 두 배가 됩니다)
total_cols = [c for c in df.columns if c.startswith("계_")]

def age_of(col):
    m = re.match(r"계_(\d+)세", col)
    return int(m.group(1)) if m else None

# 3. 그중 65세 이상 열만 ('계_65세' ~ '계_100세 이상')
elderly_cols = [c for c in total_cols if age_of(c) is not None and age_of(c) >= 65]

# 4. 동 단위로 전체 인구·고령 인구 계산
df["전체인구"] = df[total_cols].sum(axis=1)
df["고령인구"] = df[elderly_cols].sum(axis=1)

# 5. '코드' 앞 5자리 = 시군구 코드 → 시군구별로 묶어 비율 계산
df["시군구코드"] = df["코드"].str[:5]
grouped = df.groupby("시군구코드")[["전체인구", "고령인구"]].sum().reset_index()
grouped["고령화율"] = (grouped["고령인구"] / grouped["전체인구"] * 100).round(2)

# 경계 파일에서 코드 → 시군구·시도 이름 짝 만들기
names = pd.DataFrame([
    {
        "시군구코드": str(f["properties"]["코드"]),
        "시군구": f["properties"]["시군구"],
        "시도": f["properties"]["시도"],
    }
    for f in geojson["features"]
])
merged = grouped.merge(names, on="시군구코드", how="left")

# 6. 5단계 색 구간 (전국 시군구를 다섯 덩어리로 나눈 실제 경계값)
BINS = [0, 19, 23, 28, 38, 100]
LABELS = ["19% 미만", "19~23%", "23~28%", "28~38%", "38% 이상"]
COLORS = {
    "19% 미만": "#fee6ce",
    "19~23%": "#fdc086",
    "23~28%": "#f79646",
    "28~38%": "#e8590c",
    "38% 이상": "#a63603",
}
merged["단계"] = pd.cut(merged["고령화율"], bins=BINS, labels=LABELS, right=False)

# 7. 단계구분도 그리기 (배경 지도 타일 없이 경계만)
fig = px.choropleth(
    merged,
    geojson=geojson,
    locations="시군구코드",
    featureidkey="properties.코드",
    color="단계",
    category_orders={"단계": LABELS},
    color_discrete_map=COLORS,
    hover_name="시군구",
    hover_data={"고령화율": True, "시도": True, "시군구코드": False, "단계": False},
    labels={"고령화율": "65세 이상 비율(%)"},
)
fig.update_geos(fitbounds="locations", visible=False)
fig.update_layout(
    margin=dict(l=0, r=0, t=10, b=0),
    height=700,
    legend_title_text=f"65세 이상 비율 ({latest_year}년)",
)

st.plotly_chart(fig, width="stretch")

# 8. 지도 아래 순위 표 두 개
c1, c2 = st.columns(2)
cols = ["시도", "시군구", "고령화율"]
with c1:
    st.subheader("🔴 고령화율 높은 곳 10")
    st.dataframe(merged.nlargest(10, "고령화율")[cols].reset_index(drop=True))
with c2:
    st.subheader("🟢 고령화율 낮은 곳 10")
    st.dataframe(merged.nsmallest(10, "고령화율")[cols].reset_index(drop=True))
import json
import re
from pathlib import Path

import folium
import geopandas as gpd
import numpy as np
import pandas as pd
import streamlit as st
from branca.colormap import StepColormap
from streamlit_folium import st_folium


# ============================================================
# 1. 기본 설정
# ============================================================

st.set_page_config(
    page_title="대한민국 연령별 인구 지도",
    page_icon="🗺️",
    layout="wide",
)

# 기존 파일 경로에 맞게 필요한 경우 여기만 수정하세요.
GEOJSON_PATH = "./data/korea_sigungu.geojson"
DATASETS_PATH = "./data/population.csv"


# ============================================================
# 2. 지도 색상 구간
# ============================================================
#
# 고령화율:
#   < 19%
#   19~23%
#   23~28%
#   28~38%
#   >= 38%
#
# 사용자가 요청한 대로 "연도가 바뀌어도" 이 경계값은 고정.
#
# 유소년은 65세 이상 비율보다 값의 범위가 낮으므로 별도 구간 사용.
#
AGING_BINS = [0, 19, 23, 28, 38, 100]
AGING_COLORS = [
    "#edf8fb",
    "#b2e2e2",
    "#66c2a4",
    "#2ca25f",
    "#006d2c",
]

YOUTH_BINS = [0, 5, 10, 15, 20, 100]
YOUTH_COLORS = [
    "#fff7bc",
    "#fee391",
    "#fec44f",
    "#fe9929",
    "#d95f0e",
]


# ============================================================
# 3. 컬럼명 자동 탐색
# ============================================================

def find_column(df, candidates, required=True):
    """
    후보 컬럼명 중 실제 데이터에 존재하는 컬럼을 찾는다.
    공백/대소문자 차이를 어느 정도 허용한다.
    """
    normalized = {
        re.sub(r"[\s_\-]", "", str(col)).lower(): col
        for col in df.columns
    }

    for candidate in candidates:
        key = re.sub(r"[\s_\-]", "", str(candidate)).lower()
        if key in normalized:
            return normalized[key]

    if required:
        raise ValueError(
            "필요한 컬럼을 찾지 못했습니다.\n\n"
            f"찾아본 후보: {candidates}\n\n"
            f"현재 CSV 컬럼: {list(df.columns)}"
        )

    return None


def find_year_column(df):
    return find_column(
        df,
        [
            "연도",
            "년도",
            "year",
            "Year",
            "YEAR",
        ],
    )


def find_code_column(df):
    return find_column(
        df,
        [
            "시군구코드",
            "시군구 코드",
            "시군구코드5",
            "행정구역코드",
            "행정구역 코드",
            "지역코드",
            "지역 코드",
            "code",
            "Code",
            "CODE",
            "sigungu_code",
            "SIGUNGU_CD",
        ],
    )


def find_name_column(df):
    return find_column(
        df,
        [
            "시군구명",
            "시군구",
            "지역명",
            "지역",
            "행정구역명",
            "법정동명",
            "name",
            "Name",
            "SIGUNGU_NM",
        ],
    )


def find_aging_column(df):
    return find_column(
        df,
        [
            "65세이상비율",
            "65세 이상 비율",
            "65세이상 비율",
            "고령화율",
            "고령인구비율",
            "고령인구 비율",
            "aging_rate",
            "aging",
            "AgingRate",
            "65+",
        ],
    )


def find_youth_column(df):
    return find_column(
        df,
        [
            "0-14세비율",
            "0~14세비율",
            "0~14세 비율",
            "0-14세 비율",
            "유소년비율",
            "유소년 비율",
            "유소년인구비율",
            "유소년인구 비율",
            "youth_rate",
            "youth",
            "YouthRate",
            "0-14",
        ],
    )


# ============================================================
# 4. 데이터 로드
# ============================================================

@st.cache_data
def load_dataset():
    df = pd.read_csv(DATASETS_PATH, encoding="utf-8-sig")

    year_col = find_year_column(df)
    code_col = find_code_column(df)
    name_col = find_name_column(df)
    aging_col = find_aging_column(df)
    youth_col = find_youth_column(df)

    df = df.copy()

    # 연도
    df["_year"] = pd.to_numeric(df[year_col], errors="coerce").astype("Int64")

    # 코드
    #
    # 47720 -> 27720
    # 42xxx -> 51xxx
    # 45xxx -> 52xxx
    #
    # 앞자리 치환을 위해 문자열 5자리로 통일.
    df["_code"] = (
        df[code_col]
        .astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.strip()
        .str.zfill(5)
    )

    df["_code"] = df["_code"].replace(
        {
            "47720": "27720",
        }
    )

    df["_code"] = df["_code"].apply(normalize_admin_code)

    # 지역명
    df["_name"] = df[name_col].astype(str).str.strip()

    # 비율
    df["_aging"] = to_percentage(df[aging_col])
    df["_youth"] = to_percentage(df[youth_col])

    return df


def normalize_admin_code(code):
    """
    과거 행정구역 코드와 현재 경계 파일의 코드 차이를 보정한다.

    42 -> 51 : 강원
    45 -> 52 : 전북
    47720 -> 27720 : 군위군
    """
    code = str(code).strip().zfill(5)

    if code == "47720":
        return "27720"

    if code.startswith("42"):
        return "51" + code[2:]

    if code.startswith("45"):
        return "52" + code[2:]

    return code


def to_percentage(series):
    """
    데이터가
      0.19 형태이면 19로,
      19 형태이면 그대로
    처리한다.
    """
    values = pd.to_numeric(series, errors="coerce")

    if values.dropna().empty:
        return values

    # 대부분 0~1이면 비율형으로 판단
    if values.dropna().max() <= 1.0:
        values = values * 100

    return values


@st.cache_data
def load_geojson():
    return gpd.read_file(GEOJSON_PATH)


# ============================================================
# 5. GeoJSON 코드/명칭 자동 탐색
# ============================================================

def find_geo_code_column(gdf):
    candidates = [
        "id",
        "ID",
        "code",
        "CODE",
        "시군구코드",
        "시군구 코드",
        "행정구역코드",
        "행정구역 코드",
        "SIGUNGU_CD",
        "SIG_CD",
        "adm_cd",
        "ADM_CD",
        "sigungu_code",
    ]

    for col in candidates:
        if col in gdf.columns:
            return col

    # 컬럼명이 정확히 없을 때 숫자형 5자리 코드 후보 탐색
    for col in gdf.columns:
        sample = gdf[col].dropna().astype(str).str.replace(
            r"\.0$", "", regex=True
        )

        if len(sample) > 0 and sample.str.match(r"^\d{5}$").mean() > 0.5:
            return col

    return None


def find_geo_name_column(gdf):
    candidates = [
        "SIG_KOR_NM",
        "SIGUNGU_NM",
        "시군구명",
        "시군구",
        "지역명",
        "name",
        "NAME",
        "nam_ja",
        "ADM_NM",
    ]

    for col in candidates:
        if col in gdf.columns:
            return col

    return None


# ============================================================
# 6. 지도용 데이터 만들기
# ============================================================

def prepare_geo_data(gdf, year_df, metric):
    """
    GeoJSON과 선택한 연도의 데이터를 코드 기준으로 연결한다.

    매칭되지 않는 지역은 _value가 NaN이 되고
    지도에서 회색으로 표시한다.
    """
    geo = gdf.copy()

    geo_code_col = find_geo_code_column(geo)
    geo_name_col = find_geo_name_column(geo)

    if geo_code_col is None:
        raise ValueError(
            "GeoJSON에서 5자리 시군구 코드를 찾을 수 없습니다.\n"
            f"현재 GeoJSON 컬럼: {list(geo.columns)}"
        )

    geo["_map_code"] = (
        geo[geo_code_col]
        .astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.strip()
        .str.zfill(5)
        .apply(normalize_admin_code)
    )

    if geo_name_col:
        geo["_map_name"] = geo[geo_name_col].astype(str)
    else:
        geo["_map_name"] = geo["_map_code"]

    value_df = year_df[["_code", "_name", metric]].copy()
    value_df = value_df.rename(
        columns={
            "_code": "_map_code",
            "_name": "_data_name",
            metric: "_value",
        }
    )

    # 같은 코드가 여러 행이면 평균
    value_df = (
        value_df.groupby("_map_code", as_index=False)
        .agg(
            {
                "_data_name": "first",
                "_value": "mean",
            }
        )
    )

    geo = geo.merge(
        value_df,
        on="_map_code",
        how="left",
    )

    geo["_display_name"] = geo["_data_name"].fillna(geo["_map_name"])

    return geo


# ============================================================
# 7. 시도 코드
# ============================================================

SIDO_NAMES = {
    "11": "서울특별시",
    "21": "부산광역시",
    "22": "대구광역시",
    "23": "인천광역시",
    "24": "광주광역시",
    "25": "대전광역시",
    "26": "울산광역시",
    "29": "세종특별자치시",
    "31": "경기도",
    "32": "강원특별자치도",
    "33": "충청북도",
    "34": "충청남도",
    "35": "전라북도",
    "36": "전라남도",
    "37": "경상북도",
    "38": "경상남도",
    "39": "제주특별자치도",
    "51": "강원특별자치도",
    "52": "전북특별자치도",
}


def get_sido_code(code):
    code = str(code).zfill(5)

    # 현재 코드
    first2 = code[:2]

    # 옛 코드도 혹시 남아 있으면 현재 코드로 변환
    if first2 == "42":
        return "51"

    if first2 == "45":
        return "52"

    return first2


def make_sido_options(df):
    codes = sorted(
        {
            get_sido_code(code)
            for code in df["_code"].dropna().astype(str)
        }
    )

    result = {"전국": None}

    for code in codes:
        name = SIDO_NAMES.get(code, f"시도 {code}")
        result[name] = code

    return result


# ============================================================
# 8. 전국 통계
# ============================================================

def get_national_rate(year_df, metric):
    values = pd.to_numeric(year_df[metric], errors="coerce").dropna()

    if values.empty:
        return np.nan

    return values.mean()


def get_extreme_region(year_df, metric, highest=True):
    tmp = year_df[["_name", metric]].copy()
    tmp[metric] = pd.to_numeric(tmp[metric], errors="coerce")
    tmp = tmp.dropna(subset=[metric])

    if tmp.empty:
        return None, np.nan

    tmp = tmp.sort_values(metric, ascending=not highest)

    row = tmp.iloc[0]

    return row["_name"], row[metric]


# ============================================================
# 9. 지도 색상
# ============================================================

def get_color_config(metric):
    if metric == "_aging":
        return AGING_BINS, AGING_COLORS, "고령화율 (%)"

    return YOUTH_BINS, YOUTH_COLORS, "유소년 비율 (%)"


def color_for_value(value, bins, colors):
    if pd.isna(value):
        return "#bdbdbd"

    value = float(value)

    for i in range(len(bins) - 1):
        if bins[i] <= value < bins[i + 1]:
            return colors[i]

    return colors[-1]


# ============================================================
# 10. 지도 생성
# ============================================================

def build_map(
    geo,
    metric,
    selected_sido_code=None,
):
    bins, colors, legend_name = get_color_config(metric)

    map_geo = geo.copy()

    # 시도 선택
    if selected_sido_code is not None:
        map_geo = map_geo[
            map_geo["_map_code"].str[:2] == selected_sido_code
        ].copy()

    # 전국 지도
    if selected_sido_code is None:
        center = [36.35, 127.8]
        zoom = 6.5
    else:
        center = [36.3, 127.8]
        zoom = 8.0

    m = folium.Map(
        location=center,
        zoom_start=zoom,
        tiles="CartoDB positron",
        control_scale=True,
    )

    # --------------------------------------------------------
    # GeoJSON polygon
    # --------------------------------------------------------

    def style_function(feature):
        value = feature["properties"].get("_value")

        return {
            "fillColor": color_for_value(
                value,
                bins,
                colors,
            ),
            "color": "#555555",
            "weight": 0.7,
            "fillOpacity": 0.78,
        }

    def highlight_function(feature):
        return {
            "weight": 2.2,
            "color": "#111111",
            "fillOpacity": 0.9,
        }

    tooltip_fields = [
        "_display_name",
        "_value",
    ]

    tooltip_aliases = [
        "지역",
        f"{legend_name}:",
    ]

    tooltip = folium.GeoJsonTooltip(
        fields=tooltip_fields,
        aliases=tooltip_aliases,
        localize=True,
        sticky=False,
        labels=True,
        style=(
            "background-color: white;"
            "color: #222;"
            "font-family: sans-serif;"
            "font-size: 13px;"
            "padding: 8px;"
        ),
    )

    folium.GeoJson(
        data=map_geo.to_json(),
        name="지역별 지표",
        style_function=style_function,
        highlight_function=highlight_function,
        tooltip=tooltip,
        smooth_factor=0.5,
    ).add_to(m)

    # --------------------------------------------------------
    # 범례
    # --------------------------------------------------------

    colormap = StepColormap(
        colors=colors,
        index=bins,
        vmin=bins[0],
        vmax=bins[-1],
        caption=legend_name,
    )

    colormap.add_to(m)

    # --------------------------------------------------------
    # 회색 지역 설명
    # --------------------------------------------------------

    missing_count = int(map_geo["_value"].isna().sum())

    if missing_count > 0:
        notice_html = f"""
        <div style="
            position: fixed;
            bottom: 18px;
            left: 18px;
            z-index: 9999;
            background: rgba(255,255,255,0.95);
            border: 1px solid #cccccc;
            border-radius: 6px;
            padding: 8px 12px;
            font-size: 12px;
            color: #555;
            box-shadow: 0 1px 5px rgba(0,0,0,.15);
        ">
            <span style="
                display:inline-block;
                width:12px;
                height:12px;
                background:#bdbdbd;
                margin-right:6px;
                vertical-align:middle;
            "></span>
            경계 파일과 행정구역 코드가 맞지 않는 지역은
            회색으로 표시했습니다. ({missing_count}개 지역)
        </div>
        """

        m.get_root().html.add_child(
            folium.Element(notice_html)
        )

    return m, missing_count


# ============================================================
# 11. 앱 제목
# ============================================================

st.title("🗺️ 대한민국 연령별 인구 지도")

st.caption(
    "연도와 지표를 선택하면 해당 연도의 시군구별 비율을 지도에서 비교할 수 있습니다."
)


# ============================================================
# 12. 데이터 로드
# ============================================================

try:
    df = load_dataset()
    gdf = load_geojson()

except Exception as e:
    st.error("데이터를 불러오는 중 오류가 발생했습니다.")
    st.code(str(e))
    st.stop()


# ============================================================
# 13. 기본 검증
# ============================================================

years = sorted(
    [
        int(x)
        for x in df["_year"].dropna().unique()
    ]
)

if not years:
    st.error("CSV에서 연도 데이터를 찾을 수 없습니다.")
    st.stop()


# ============================================================
# 14. 상단 선택 영역
# ============================================================

control_col1, control_col2 = st.columns([1.2, 1])

with control_col1:
    metric_label = st.selectbox(
        "지표",
        [
            "65세 이상 (고령화율)",
            "0~14세 (유소년 비율)",
        ],
        index=0,
    )

with control_col2:
    sido_options = make_sido_options(df)

    selected_sido_name = st.selectbox(
        "시도",
        list(sido_options.keys()),
        index=0,
    )

selected_sido_code = sido_options[selected_sido_name]

if metric_label.startswith("65세"):
    metric = "_aging"
else:
    metric = "_youth"


# ============================================================
# 15. 연도 슬라이더
# ============================================================

selected_year = st.slider(
    "연도",
    min_value=min(years),
    max_value=max(years),
    value=max(years),
    step=1,
)

st.caption(
    f"현재 선택: **{selected_year}년 · {metric_label} · {selected_sido_name}**"
)


# ============================================================
# 16. 선택 연도 데이터
# ============================================================

year_df = df[
    df["_year"] == selected_year
].copy()

if selected_sido_code is not None:
    year_df = year_df[
        year_df["_code"].str[:2] == selected_sido_code
    ].copy()


# ============================================================
# 17. 지표 카드
# ============================================================

# 전국 지표는 시도 필터와 무관하게 계산
national_year_df = df[
    df["_year"] == selected_year
].copy()

national_rate = get_national_rate(
    national_year_df,
    metric,
)

highest_name, highest_value = get_extreme_region(
    national_year_df,
    metric,
    highest=True,
)

lowest_name, lowest_value = get_extreme_region(
    national_year_df,
    metric,
    highest=False,
)

card1, card2, card3 = st.columns(3)

with card1:
    if pd.isna(national_rate):
        st.metric(
            "전국 고령화율" if metric == "_aging" else "전국 유소년 비율",
            "-",
        )
    else:
        st.metric(
            "전국 고령화율" if metric == "_aging" else "전국 유소년 비율",
            f"{national_rate:.1f}%",
        )

with card2:
    if highest_name is None:
        st.metric("가장 높은 시군구", "-")
    else:
        st.metric(
            "가장 높은 시군구",
            highest_name,
            f"{highest_value:.1f}%",
        )

with card3:
    if lowest_name is None:
        st.metric("가장 낮은 시군구", "-")
    else:
        st.metric(
            "가장 낮은 시군구",
            lowest_name,
            f"{lowest_value:.1f}%",
        )


# ============================================================
# 18. 지도 데이터 결합
# ============================================================

map_geo = prepare_geo_data(
    gdf,
    year_df,
    metric,
)


# ============================================================
# 19. 지도
# ============================================================

m, missing_count = build_map(
    map_geo,
    metric,
    selected_sido_code,
)

st_folium(
    m,
    use_container_width=True,
    height=720,
    returned_objects=[],
)


# ============================================================
# 20. 지도 아래 안내
# ============================================================

if missing_count > 0:
    st.warning(
        f"⚠️ 현재 연도의 데이터 중 {missing_count}개 지역은 "
        "현재 경계 파일의 행정구역 코드와 일치하지 않아 "
        "지도에서 회색으로 표시했습니다. "
        "과거 코드 42→51, 45→52 및 군위군 47720→27720 "
        "변환을 적용했지만, 그래도 일치하지 않는 지역은 "
        "별도로 제외했습니다."
    )
else:
    st.caption(
        "모든 지역의 행정구역 코드가 현재 경계 파일과 정상적으로 연결되었습니다."
    )


# ============================================================
# 21. 선택한 연도의 간단한 표
# ============================================================

with st.expander("선택한 연도의 지역별 수치 보기"):
    table = map_geo[
        [
            "_display_name",
            "_value",
            "_map_code",
        ]
    ].copy()

    table.columns = [
        "시군구",
        "비율(%)",
        "행정구역코드",
    ]

    table["비율(%)"] = pd.to_numeric(
        table["비율(%)"],
        errors="coerce",
    ).round(1)

    table = table.sort_values(
        "비율(%)",
        ascending=False,
        na_position="last",
    )

    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
    )


# ============================================================
# 22. 색상 구간 설명
# ============================================================

if metric == "_aging":
    st.caption(
        "고령화율 색상 구간: "
        "19% · 23% · 28% · 38% (연도와 관계없이 고정)"
    )
else:
    st.caption(
        "유소년 비율 색상 구간: "
        "5% · 10% · 15% · 20% (유소년 비율에 맞춘 별도 구간)"
    )
