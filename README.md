# 🧩 Gerber PCB Importer for Blender (v2)

**Author:** Albert Hycentte  
**Version:** 2  

---

## 🧠 Overview

This Blender add-on allows you to **import Gerber PCB files (`.gbr`)** directly into Blender for visualization and routing design.  
Version 2 fixes a key issue where `mathlib` was mistakenly referenced instead of `mathutils`.  
Although `Vector` wasn’t actively used in the script, this correction ensures full compatibility and consistency with Blender’s API.

---

## ⚙️ Installation Guide

### 1. Remove the Old Add-on (if previously installed)
1. Go to **Edit → Preferences → Add-ons**  
2. Search for **“Gerber”**  
3. Click the **❌ X** button to remove it  

### 2. Install the Updated Version
1. Save the corrected Python file (this `.py` file)  
2. In Blender, go to **Edit → Preferences → Add-ons → Install**  
3. Navigate to your saved file and **install** it  

### 3. Enable the Add-on
- In the Add-ons list, check the box next to:  
  **Import-Export: Gerber PCB Importer**

### 4. Test the Add-on
1. Go to **File → Import**  
2. You should now see a new option:  
   **Gerber PCB (.gbr)**

---

## 🚀 You’re All Set!

The add-on is now ready to use for importing your PCB Gerber files directly into Blender.

---

