#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Public launcher for OSCam Patch Manager. Run: python main.py"""
import os
import platform

if platform.system() == "Windows":
    os.environ.setdefault("QT_QUICK_BACKEND", "software")
    os.environ.setdefault("QT_OPENGL", "software")
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")

from startup import main

if __name__ == "__main__":
    main()
