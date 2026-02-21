# ============================================================================
# DeckDock Crawler GUI — Container Image
# Builds a self-contained environment with all crawling, extraction,
# and browser-automation dependencies pre-installed.
# ============================================================================

FROM python:3.12-slim AS base

LABEL maintainer="DeckDock Contributors"
LABEL description="DeckDock Crawler GUI with Playwright, 7-Zip, unrar, and chdman"

# ----------------------------------------------------------------------------
# System packages
# ----------------------------------------------------------------------------
# - p7zip-full: 7z archive support
# - chromium: headless browser for Playwright
# - fonts-liberation: required font family for Chromium rendering
# - curl: general HTTP utility
# - unrar-free: RAR extraction support
# - mame-tools: provides chdman for CHD conversion
# ----------------------------------------------------------------------------
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        p7zip-full \
        chromium \
        fonts-liberation \
        curl \
        unrar-free \
        mame-tools \
    && rm -rf /var/lib/apt/lists/*

# ----------------------------------------------------------------------------
# Python packages
# ----------------------------------------------------------------------------
RUN pip install --no-cache-dir \
    requests \
    beautifulsoup4 \
    py7zr \
    rarfile \
    playwright

# ----------------------------------------------------------------------------
# Playwright browser install
# ----------------------------------------------------------------------------
# Install Chromium via Playwright along with its OS-level dependencies.
# Some deps overlap with what we installed above — --with-deps ensures
# nothing is missing for Playwright's own Chromium build.
# ----------------------------------------------------------------------------
RUN playwright install chromium --with-deps

# ----------------------------------------------------------------------------
# Non-root user
# ----------------------------------------------------------------------------
RUN groupadd --gid 1000 crawler && \
    useradd --uid 1000 --gid crawler --create-home crawler

# ----------------------------------------------------------------------------
# Application code
# ----------------------------------------------------------------------------
WORKDIR /app
COPY crawler/ ./

# Config will be bind-mounted at runtime (see docker-compose.yml)
ENV DECKDOCK_CONFIG=/config/config.env

# Ensure the mount-point directory exists and is readable
RUN mkdir -p /config && chown crawler:crawler /config

# Drop to non-root
USER crawler

# ----------------------------------------------------------------------------
# Runtime
# ----------------------------------------------------------------------------
EXPOSE 7072

CMD ["python3", "crawler-gui.py"]
