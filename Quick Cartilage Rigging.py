"""
快速软骨绑定
功能：骨骼细分、FK绑定、阻尼追踪约束设置
作者：烟囱鸭
"""

bl_info = {
    "name": "🦴快速软骨绑定",
    "author": "烟囱鸭",
    "version": (1, 1, 0),
    "blender": (2, 80, 0),
    "location": "3D View > UI > Damped Track",
    "description": "提供骨骼自动细分、FK绑定和阻尼追踪约束设置功能",
    "warning": "目前仅在4.5版本进行测试",
    "doc_url": "",
    "category": "Rigging",
}

import bpy
import math
import re
import urllib.request

def _to_raw_github_url(url: str) -> str:
    """将 GitHub blob 页面 URL 转换为 raw 内容 URL"""
    try:
        # 例如: https://github.com/user/repo/blob/branch/path -> https://raw.githubusercontent.com/user/repo/branch/path
        m = re.match(r"https://github.com/([^/]+)/([^/]+)/blob/([^/]+)/(.*)", url)
        if m:
            user, repo, branch, path = m.groups()
            return f"https://raw.githubusercontent.com/{user}/{repo}/{branch}/{path}"
        return url
    except Exception:
        return url

def _fetch_text(url: str) -> str:
    """获取远程文本内容，添加基本的 User-Agent"""
    req = urllib.request.Request(_to_raw_github_url(url), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = resp.read()
        # 尝试按 utf-8 解码
        try:
            return data.decode('utf-8')
        except Exception:
            return data.decode('latin-1', errors='ignore')

def _parse_version_tuple(text: str):
    """从文本中解析类似 1.2.3 的版本元组，无法解析则返回 None"""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    if not m:
        return None
    return tuple(int(p) for p in m.groups())

def _is_newer_version(remote, local):
    """比较远程与本地版本元组，远程更大返回 True"""
    return remote is not None and local is not None and remote > local

# 全局变量存储当前面板类别
current_panel_category = "Damped Track"

# 动态面板注册缓存与助手函数（支持实时启用/禁用）
panel_classes_cache = {}
registered_panels = {}

def register_panel(category):
    cls = panel_classes_cache.get(category)
    if not cls:
        cls = get_panel_class(category)
        panel_classes_cache[category] = cls
    try:
        bpy.utils.register_class(cls)
    except RuntimeError:
        # 已注册时忽略
        pass
    registered_panels[category] = cls

def unregister_panel(category):
    cls = registered_panels.get(category) or panel_classes_cache.get(category)
    if cls:
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            # 未注册时忽略
            pass
        registered_panels.pop(category, None)

def apply_panel_prefs(show_n, show_tool):
    # N面板（右侧侧栏）
    if show_n:
        register_panel("Damped Track")
    else:
        unregister_panel("Damped Track")
    # 工具面板（作为分类“Tool”的页签）
    if show_tool:
        register_panel("Tool")
    else:
        unregister_panel("Tool")

def update_panel_registration(self, context):
    # 偏好切换时实时应用
    try:
        apply_panel_prefs(self.show_in_n_panel, self.show_in_tool_panel)
    except Exception as e:
        print(f"更新面板注册失败: {e}")
    # 面板注册或注销后强制重绘3D视图区域
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
    except Exception as e:
        print(f"重绘视图失败: {e}")

def get_unique_base_name(original_base_name, existing_bones):
    """获取一个不与现有骨骼冲突的基础名称"""
    counter = 1
    test_base_name = original_base_name
    while True:
        # 检查是否有任何现有的骨骼名以 test_base_name 开头且后面跟着 .xxx 的数字后缀
        conflict_found = False
        for bone in existing_bones:
            if bone.name.startswith(test_base_name + '.'):
                suffix = bone.name[len(test_base_name)+1:]
                if suffix.isdigit():
                    conflict_found = True
                    break
        
        if not conflict_found:
            return test_base_name
        
        # 如果有冲突，添加后缀并递增计数器
        test_base_name = f"{original_base_name}_{counter}"
        counter += 1

# 插件偏好设置
class DampedTrackAddonPreferences(bpy.types.AddonPreferences):
    # 动态ID与模块名一致，确保重命名脚本后偏好仍显示
    bl_idname = __name__

    # 选择面板显示位置 - 使用复选框以支持同时显示
    show_in_n_panel: bpy.props.BoolProperty(
        name="N面板",
        description="在N面板中显示阻尼追踪面板",
        default=True,
        update=update_panel_registration
    )
    show_in_tool_panel: bpy.props.BoolProperty(
        name="工具面板",
        description="在工具面板中显示阻尼追踪功能",
        default=False,
        update=update_panel_registration
    )
    
    # 默认控制器属性
    default_circle_scale: bpy.props.FloatProperty(
        name="默认圆环缩放",
        description="新创建控制器的默认圆环缩放值",
        min=0.0,
        max=2.0,
        default=1.0,
        soft_min=0.0,
        soft_max=5.0,
    )
    
    default_damped_track_influence: bpy.props.FloatProperty(
        name="默认追踪强度",
        description="新创建控制器的默认追踪强度值",
        min=0.0,
        max=1.0,
        default=0.6,
        soft_min=0.0,
        soft_max=1.0,
    )
    
    # 右键菜单设置
    enable_right_click_menu: bpy.props.BoolProperty(
        name="启用右键菜单",
        description="在对象、编辑骨架和姿态模式下启用右键菜单",
        default=True
    )

    def draw(self, context):
        layout = self.layout
        
        # 第一行：显示选项
        row1 = layout.row()
        col1 = row1.column()
        col1.prop(self, "show_in_n_panel")
        col2 = row1.column()
        col2.prop(self, "show_in_tool_panel")
        col3 = row1.column()
        col3.prop(self, "enable_right_click_menu")
        
        # 第二行：默认控制器属性
        row2 = layout.row()
        col4 = row2.column()
        col4.prop(self, "default_circle_scale")
        col5 = row2.column()
        col5.prop(self, "default_damped_track_influence")
        
        # 提示：更改立即生效
        layout.separator()
        layout.label(text="提示：工具面板的取消启用，重启下N面板即可", icon='INFO')

# 用于防止递归更新的标志
_visibility_update_lock = False

# 用于防止递归更新的标志
_visibility_update_lock = False

def update_ctrl_bone_visibility(self, context):
    """更新控制骨骼的可见性"""
    global _visibility_update_lock
    
    # 防止递归更新
    if _visibility_update_lock:
        return
        
    # 防止在某些情况下可能的错误
    try:
        if context and hasattr(context, 'object') and context.object and context.object.type == 'ARMATURE':
            armature = context.object.data
            # 获取当前属性所属的骨骼（即调用此更新函数的骨骼）
            # 在这种情况下，self是MyArmatureProperties实例，它的id_data是PoseBone
            pose_bone = self.id_data
            
            if pose_bone and hasattr(pose_bone, 'name'):
                # 从控制骨骼名称中提取基础名称 (例如: 从 "ctr_arm.001" 中获取 "arm")
                bone_name = pose_bone.name.replace('ctr_', '')
                bone_name_parts = bone_name.split('.')
                if len(bone_name_parts) > 1 and bone_name_parts[-1].isdigit():
                    base_name = '.'.join(bone_name_parts[:-1])
                else:
                    base_name = bone_name

                # 生成集合名称
                collection_name_all = f"ctrl_{base_name}_all"
                collection_name_first = f"ctrl_{base_name}_first"
                
                # 检查集合是否存在（使用 collections_all）
                if collection_name_all in armature.collections_all and collection_name_first in armature.collections_all:
                    all_collection = armature.collections_all[collection_name_all]
                    first_collection = armature.collections_all[collection_name_first]
                    
                    _visibility_update_lock = True
                    try:
                        # 如果用户勾选了"独显第一根"，取消"显示所有"
                        if getattr(pose_bone.my_tool_props, 'show_only_first_ctrl_bone', False):
                            pose_bone.my_tool_props.show_all_ctrl_bones = False
                        # 如果用户勾选了"显示所有"，取消"独显第一根"
                        elif getattr(pose_bone.my_tool_props, 'show_all_ctrl_bones', False):
                            pose_bone.my_tool_props.show_only_first_ctrl_bone = False

                        # 根据最新状态设置集合可见性
                        if getattr(pose_bone.my_tool_props, 'show_only_first_ctrl_bone', False):
                            all_collection.is_visible = False
                            first_collection.is_visible = True
                        elif getattr(pose_bone.my_tool_props, 'show_all_ctrl_bones', False):
                            all_collection.is_visible = True
                            first_collection.is_visible = False
                        else:
                            all_collection.is_visible = False
                            first_collection.is_visible = False
                    finally:
                        _visibility_update_lock = False
    except Exception as e:
        # 确保即使出错也要重置锁
        _visibility_update_lock = False
        print(f"更新骨骼可见性时出错: {e}")
        pass


# --- Property Group for Custom Properties (Robust UI) ---
class MyArmatureProperties(bpy.types.PropertyGroup):
    damped_track_influence: bpy.props.FloatProperty(
        name="难崩系数",
        description="系数越高越难崩住",
        min=0.0,
        max=1.0,
        default=0.6,
        soft_min=0.0,
        soft_max=1.0,
    )
    circle_scale: bpy.props.FloatProperty(
        name="圆环缩放",
        description="动态缩放所有圆环控制器的大小",
        min=0.0,
        max=5.0,
        default=1.0,
        soft_min=0.0,
        soft_max=5.0,
    )
    show_all_ctrl_bones: bpy.props.BoolProperty(
        name="显示所有控制骨骼",
        description="显示所有控制骨骼",
        default=True,
        update=update_ctrl_bone_visibility
    )
    show_only_first_ctrl_bone: bpy.props.BoolProperty(
        name="独显第一根控制骨骼",
        description="只显示第一根控制骨骼，隐藏其他控制骨骼",
        default=False,
        update=update_ctrl_bone_visibility
    )

# --- Mode Switch Operators ---
class WM_OT_SwitchObjectMode(bpy.types.Operator):
    bl_idname = "wm.switch_object_mode"
    bl_label = "Object Mode"
    bl_description = "切换到物体模式"
    def execute(self, context):
        bpy.ops.object.mode_set(mode='OBJECT')
        return {'FINISHED'}

class WM_OT_SwitchEditMode(bpy.types.Operator):
    bl_idname = "wm.switch_edit_mode"
    bl_label = "Edit Mode"
    bl_description = "切换到编辑模式"
    def execute(self, context):
        bpy.ops.object.mode_set(mode='EDIT')
        return {'FINISHED'}

class WM_OT_SwitchPoseMode(bpy.types.Operator):
    bl_idname = "wm.switch_pose_mode"
    bl_label = "Pose Mode"
    bl_description = "切换到姿态模式"
    def execute(self, context):
        bpy.ops.object.mode_set(mode='POSE')
        return {'FINISHED'}

# --- Main Operators ---
class SubdivideFibOperator(bpy.types.Operator):
    bl_idname = "armature.subdivide_fib"
    bl_label = "斐波那契细分"
    bl_description = "使用斐波那契数列分割选中的骨骼，产生由疏到密的链条，适合做尾巴"
    bl_options = {'REGISTER', 'UNDO'}

    segments: bpy.props.IntProperty(
        name="段数",
        description="要分割的段数",
        default=5,
        min=1,
        max=100
    )
    
    coefficient: bpy.props.FloatProperty(
        name="系数",
        description="斐波那契系数",
        default=1.0,
        min=0.01,
        max=10.0
    )
    
    auto_execute: bpy.props.BoolProperty(
        name="自动执行",
        description="执行细分后自动执行FK绑定和阻尼追踪",
        default=False
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_ARMATURE' and context.object and context.object.type == 'ARMATURE'

    def invoke(self, context, event):
        # 使用Alt键状态作为自动执行的默认值
        self.auto_execute = event.alt
        # 使用场景中的当前值作为默认值
        self.segments = context.scene.fib_segments
        self.coefficient = context.scene.fib_coefficient
        return context.window_manager.invoke_props_dialog(self, width=300)

    def execute(self, context):
        # 更新场景属性以保持一致性
        context.scene.fib_segments = self.segments
        context.scene.fib_coefficient = self.coefficient
        
        segments = self.segments
        coefficient = self.coefficient
        obj = context.object
        arm = obj.data
        
        selected_bones_at_start = [b for b in arm.edit_bones if b.select]
        last_first_bone = None

        for bone in selected_bones_at_start:
            parent = bone.parent
            children = [c for c in arm.edit_bones if c.parent == bone]
            head, tail, length = bone.head.copy(), bone.tail.copy(), bone.length
            if length == 0: continue
            
            vec = tail - head
            dir_vec = vec.normalized()
            
            fib = [1.0, 1.0]
            for i in range(2, segments):
                fib.append(fib[-1] + coefficient * fib[-2])
            fib = fib[:segments]
            fib = fib[::-1]
            sum_f = sum(fib)
            
            new_bones, current_head = [], head
            # Extract base name and find a unique base name that doesn't conflict with existing bones
            original_base_name = bone.name.rsplit('.', 1)[0] if '.' in bone.name and bone.name.rsplit('.', 1)[1].isdigit() else bone.name
            base_name = get_unique_base_name(original_base_name, arm.edit_bones)
            
            for i in range(segments):
                seg_len = (fib[i] / sum_f) * length
                current_tail = current_head + dir_vec * seg_len
                new_bone = arm.edit_bones.new(f"{base_name}.{i+1:03d}")
                new_bone.head, new_bone.tail = current_head, current_tail
                new_bone.use_deform = True
                new_bone.parent = new_bones[-1] if new_bones else parent
                new_bones.append(new_bone)
                current_head = current_tail
            
            if new_bones:
                last_first_bone = new_bones[0]

            extra_bone = arm.edit_bones.new(f"{base_name}.000")
            extra_bone.head = new_bones[-1].tail
            extra_bone.tail = new_bones[-1].tail + dir_vec * new_bones[-1].length
            extra_bone.use_deform = True
            extra_bone.parent = new_bones[-1]
            
            for child in children:
                child.parent = extra_bone
            arm.edit_bones.remove(bone)
        
        for b in arm.edit_bones: b.select = False
        if last_first_bone:
            last_first_bone.select = True
            arm.edit_bones.active = last_first_bone

        # 根据auto_execute标志决定是否自动执行完整流程
        if self.auto_execute:
            # 立即执行FK绑定
            bpy.ops.armature.setup_control_rig()
            
            # 切换到姿态模式以执行软骨绑定
            bpy.ops.object.mode_set(mode='POSE')
            
            # 立即执行软骨绑定
            bpy.ops.armature.apply_pose_setup()
            
            self.report({'INFO'}, "已完成：斐波那契细分 -> FK绑定 -> 阻尼追踪")
        else:
            # 询问是否执行FK绑定
            context.window_manager.popup_menu(self.show_continue_dialog_fib, title="执行FK绑定?", icon='INFO')
        
        return {'FINISHED'}
    
    def show_continue_dialog_fib(self, menu, context):
        layout = menu.layout
        row = layout.row()
        row.label(text="是否继续执行FK绑定?", icon='QUESTION')
        
        row = layout.row()
        row.operator_context = 'EXEC_DEFAULT'
        row.operator("armature.setup_control_rig", text="是", icon='CHECKMARK')
        
        row.operator_context = 'INVOKE_DEFAULT'
        row.operator("wm.close_panel", text="否", icon='X')

class SubdivideAverageOperator(bpy.types.Operator):
    bl_idname = "armature.subdivide_average"
    bl_label = "平均细分"
    bl_description = "将选中的骨骼平均分割为设定的段数"
    bl_options = {'REGISTER', 'UNDO'}

    segments: bpy.props.IntProperty(
        name="段数",
        description="要分割的段数",
        default=5,
        min=1,
        max=100
    )
    
    auto_execute: bpy.props.BoolProperty(
        name="自动执行",
        description="执行细分后自动执行FK绑定和阻尼追踪",
        default=False
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_ARMATURE' and context.object and context.object.type == 'ARMATURE'

    def invoke(self, context, event):
        # 使用Alt键状态作为自动执行的默认值
        self.auto_execute = event.alt
        # 使用场景中的当前值作为默认值
        self.segments = context.scene.fib_segments
        return context.window_manager.invoke_props_dialog(self, width=300)

    def execute(self, context):
        # 更新场景属性以保持一致性
        context.scene.fib_segments = self.segments
        
        segments = self.segments
        obj = context.object
        arm = obj.data

        selected_bones_at_start = [b for b in arm.edit_bones if b.select]
        last_first_bone = None

        for bone in selected_bones_at_start:
            parent = bone.parent
            children = [c for c in arm.edit_bones if c.parent == bone]
            head, tail, length = bone.head.copy(), bone.tail.copy(), bone.length
            if length == 0: continue

            vec = tail - head
            dir_vec = vec.normalized()
            segment_length = length / segments
            
            new_bones, current_head = [], head
            # Extract base name and find a unique base name that doesn't conflict with existing bones
            original_base_name = bone.name.rsplit('.', 1)[0] if '.' in bone.name and bone.name.rsplit('.', 1)[1].isdigit() else bone.name
            base_name = get_unique_base_name(original_base_name, arm.edit_bones)

            for i in range(segments):
                current_tail = current_head + dir_vec * segment_length
                new_bone = arm.edit_bones.new(f"{base_name}.{i+1:03d}")
                new_bone.head, new_bone.tail = current_head, current_tail
                new_bone.use_deform = True
                new_bone.parent = new_bones[-1] if new_bones else parent
                new_bones.append(new_bone)
                current_head = current_tail

            if new_bones:
                last_first_bone = new_bones[0]

            for child in children:
                child.parent = new_bones[-1]
            arm.edit_bones.remove(bone)

        for b in arm.edit_bones: b.select = False
        if last_first_bone:
            last_first_bone.select = True
            arm.edit_bones.active = last_first_bone

        # 根据auto_execute标志决定是否自动执行完整流程
        if self.auto_execute:
            # 立即执行FK绑定
            bpy.ops.armature.setup_control_rig()
            
            # 切换到姿态模式以执行阻尼追踪
            bpy.ops.object.mode_set(mode='POSE')
            
            # 立即执行阻尼追踪
            bpy.ops.armature.apply_pose_setup()
            
            self.report({'INFO'}, "已完成：平均细分 -> FK绑定 -> 阻尼追踪")
        else:
            # 询问是否执行FK绑定
            context.window_manager.popup_menu(self.show_continue_dialog_avg, title="执行FK绑定?", icon='INFO')
        
        return {'FINISHED'}

# --- Update Check Operator ---
class WM_OT_CheckAddonUpdate(bpy.types.Operator):
    bl_idname = "wm.check_addon_update"
    bl_label = "检查更新"
    bl_description = "从远程版本文件比对当前版本，必要时下载并覆盖更新"
    bl_options = {'REGISTER', 'UNDO'}

    # 供确认弹窗显示的远程版本和下载地址
    new_version_str: bpy.props.StringProperty(default="")
    script_url: bpy.props.StringProperty(default="")

    def draw(self, context):
        layout = self.layout
        if self.new_version_str:
            layout.label(text=f"发现新版本：{self.new_version_str}", icon='INFO')
            layout.label(text="点击确定将下载并覆盖当前脚本，然后重载脚本。", icon='FILE_SCRIPT')
        else:
            layout.label(text="未检测到新版本。", icon='INFO')

    def invoke(self, context, event):
        try:
            version_url = "https://github.com/yancongya/publish/blob/main/Quick%20Cartilage%20Rigging/version.txt"
            script_url = "https://github.com/yancongya/publish/blob/main/Quick%20Cartilage%20Rigging/Quick%20Cartilage%20Rigging.py"
            remote_text = _fetch_text(version_url)
            remote_ver = _parse_version_tuple(remote_text)
            # 读取本地版本：直接使用本模块的 bl_info
            local_ver = tuple(bl_info.get('version', (0, 0, 0)))
            if remote_ver is None:
                self.report({'ERROR'}, "远程版本文件解析失败")
                return {'CANCELLED'}
            if _is_newer_version(remote_ver, local_ver):
                self.new_version_str = '.'.join(map(str, remote_ver))
                self.script_url = script_url
                return context.window_manager.invoke_props_dialog(self, width=380)
            else:
                self.report({'INFO'}, "已经是最新版本")
                return {'CANCELLED'}
        except Exception as e:
            self.report({'ERROR'}, f"检查更新失败: {e}")
            return {'CANCELLED'}

    def execute(self, context):
        # 下载并覆盖脚本，然后重载
        try:
            url = self.script_url or "https://github.com/yancongya/publish/blob/main/Quick%20Cartilage%20Rigging/Quick%20Cartilage%20Rigging.py"
            content = _fetch_text(url)
            # 写入当前脚本文件
            addon_file = __file__
            with open(addon_file, 'w', encoding='utf-8', newline='\n') as f:
                f.write(content)
            # 重载所有脚本，使更新生效
            try:
                bpy.ops.script.reload()
            except Exception:
                pass
            self.report({'INFO'}, "更新完成并已尝试重载脚本")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"更新失败: {e}")
            return {'CANCELLED'}
    
    def show_continue_dialog_avg(self, menu, context):
        layout = menu.layout
        row = layout.row()
        row.label(text="是否继续执行FK绑定?", icon='QUESTION')
        
        row = layout.row()
        row.operator_context = 'EXEC_DEFAULT'
        row.operator("armature.setup_control_rig", text="是", icon='CHECKMARK')
        
        row.operator_context = 'INVOKE_DEFAULT'
        row.operator("wm.close_panel", text="否", icon='X')

class SetupControlRigOperator(bpy.types.Operator):
    bl_idname = "armature.setup_control_rig"
    bl_label = "2.生成FK绑定"
    bl_description = "为当前骨骼链生成一套FK控制器、父子关系和自定义图形"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_ARMATURE' and context.object and context.object.type == 'ARMATURE'

    def execute(self, context):
        obj = context.object
        arm = obj.data
        edit_bones = arm.edit_bones

        active_bone = context.active_bone
        if not active_bone:
            self.report({'WARNING'}, "请先选择链中的一根骨骼")
            return {'CANCELLED'}

        # 从活动骨骼中提取基础名称部分，考虑可能有下划线后缀的情况
        bone_name_parts = active_bone.name.split('.')
        if len(bone_name_parts) > 1 and bone_name_parts[-1].isdigit():
            # 如果骨骼名格式为 base_name.number，则取除了数字后缀的部分
            original_part = '.'.join(bone_name_parts[:-1])
        else:
            original_part = active_bone.name
            
        # 处理可能包含下划线的名称 (如 bone_1.001)
        base_parts = original_part.rsplit('_', 1)
        if len(base_parts) > 1 and base_parts[1].isdigit():
            # 如果名称包含下划线数字后缀，尝试找到匹配的名称模式
            potential_base = base_parts[0]
            # 检查是否这种命名模式存在，否则回退到原名
            matching_bones = [b for b in edit_bones if b.name.startswith(potential_base + '.')]
            if matching_bones:
                base_name = potential_base
            else:
                base_name = original_part
        else:
            base_name = original_part
        
        deform_chain = [b for b in edit_bones if b.name.startswith(base_name + '.') and b.name.split('.')[-1].isdigit() and int(b.name.split('.')[-1]) > 0]
        deform_chain.sort(key=lambda b: int(b.name.split('.')[-1]))
        tip_bone = edit_bones.get(base_name + ".000")
        
        chain_to_duplicate = deform_chain + ([tip_bone] if tip_bone else [])

        if len(chain_to_duplicate) < 2:
            self.report({'WARNING'}, f"根据 '{active_bone.name}' 未找到足够长的骨骼链 (至少需要2节)")
            return {'CANCELLED'}

        new_bone_map = {}
        for old_bone in chain_to_duplicate:
            new_bone = arm.edit_bones.new(old_bone.name + "_temp_dup")
            new_bone.head, new_bone.tail, new_bone.roll = old_bone.head.copy(), old_bone.tail.copy(), old_bone.roll
            new_bone_map[old_bone.name] = new_bone

        for old_bone in chain_to_duplicate:
            if old_bone.parent and old_bone.parent.name in new_bone_map:
                new_bone_map[old_bone.name].parent = new_bone_map[old_bone.parent.name]

        duplicated_deform_bones = [new_bone_map[b.name] for b in deform_chain]
        duplicated_tip_bone = new_bone_map.get(tip_bone.name) if tip_bone else None

        num_controls = len(duplicated_deform_bones)
        control_bone_names = []
        for i in range(num_controls):
            ctrl = duplicated_deform_bones[i]
            new_name = f"ctr_{base_name}.{i+1:03d}"
            ctrl.name, ctrl.use_deform = new_name, False
            control_bone_names.append(new_name)
        
        if duplicated_tip_bone:
            edit_bones.remove(duplicated_tip_bone)
        
        # 首先找到原始骨骼的父骨骼，以便后续将控制链连接到正确位置
        original_chain_start = chain_to_duplicate[0] if chain_to_duplicate else None
        original_parent = original_chain_start.parent if original_chain_start else None

        for name in control_bone_names:
            ctrl_bone = edit_bones.get(name)
            if ctrl_bone: ctrl_bone.parent = None

        first_control_bone_edit = edit_bones.get(control_bone_names[0])
        if not first_control_bone_edit: return {'CANCELLED'}
        
        # 如果原始骨骼链有父骨骼，则将整个控制链连接到该父骨骼上
        if original_parent and first_control_bone_edit:
            first_control_bone_edit.parent = original_parent

        radius = (first_control_bone_edit.length * obj.scale.x) / 2
        ctr_base_name = control_bone_names[0].split('.')[0]
        shape_name = f"cir_{ctr_base_name}"

        bpy.ops.object.mode_set(mode='OBJECT')
        if shape_name in bpy.data.objects:
            bpy.data.objects.remove(bpy.data.objects[shape_name], do_unlink=True)

        bpy.ops.mesh.primitive_circle_add(radius=radius, vertices=32, fill_type='NOTHING', location=obj.location)
        cir_shap = context.active_object
        cir_shap.name = shape_name
        cir_shap.rotation_euler = (math.radians(90), 0, 0)
        cir_shap.hide_render = True
        cir_shap.hide_viewport = True # Compatibility fix for 4.x
        mod = cir_shap.modifiers.new(type='WIREFRAME', name='Wire')
        mod.thickness, mod.use_replace = 0.02, False

        context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode='POSE')
        
        scale_prop_name = "circle_scale"
        scale_controller_bone_name = control_bone_names[0]
        scale_controller_bone = obj.pose.bones.get(scale_controller_bone_name)

        if scale_controller_bone:
            # 从偏好设置获取默认圆环缩放值
            try:
                addon_prefs = context.preferences.addons.get(__name__ if __name__ != "__main__" else "damped_track_addon")
                if addon_prefs and hasattr(addon_prefs, 'preferences') and addon_prefs.preferences:
                    default_circle_scale = addon_prefs.preferences.default_circle_scale
                else:
                    default_circle_scale = 1.0
            except:
                default_circle_scale = 1.0
            scale_controller_bone.my_tool_props.circle_scale = default_circle_scale

        for name in control_bone_names:
            pb = obj.pose.bones.get(name)
            if pb:
                pb.custom_shape = cir_shap
                pb.custom_shape_rotation_euler = (math.radians(90), 0, 0)
                if scale_controller_bone:
                    for i in range(2):
                        fcurve = pb.driver_add("custom_shape_scale_xyz", i)
                        driver = fcurve.driver
                        driver.expression = "scale_var"
                        var = driver.variables.new()
                        var.name, var.type = "scale_var", 'SINGLE_PROP'
                        var.targets[0].id = obj
                        var.targets[0].data_path = f'pose.bones["{scale_controller_bone_name}"].my_tool_props.{scale_prop_name}'

        bpy.ops.object.mode_set(mode='EDIT')
        for i in range(num_controls, 1, -1):
            ctr_bone = edit_bones.get(f"ctr_{base_name}.{i:03d}")
            parent_def_bone = edit_bones.get(f"{base_name}.{i-1:03d}")
            if ctr_bone and parent_def_bone: ctr_bone.parent = parent_def_bone

        def_bone_001 = edit_bones.get(f"{base_name}.001")
        ctr_bone_001 = edit_bones.get(f"ctr_{base_name}.001")
        if def_bone_001 and ctr_bone_001: def_bone_001.parent = ctr_bone_001

        # --- Final Automation Step ---
        bpy.ops.object.mode_set(mode='POSE')
        for b in arm.bones: b.select = False
        
        first_control_bone_data = arm.bones.get(control_bone_names[0])
        if first_control_bone_data:
            first_control_bone_data.select = True
            arm.bones.active = first_control_bone_data

        # 创建骨骼集合并分配控制骨骼
        try:
            # 获取或创建骨骼集合
            collection_name_all = f"ctrl_{base_name}_all"
            collection_name_first = f"ctrl_{base_name}_first"
            
            # 删除可能已存在的同名集合
            if collection_name_all in arm.collections:
                arm.collections.remove(arm.collections[collection_name_all])
            if collection_name_first in arm.collections:
                arm.collections.remove(arm.collections[collection_name_first])
            
            # 创建骨骼集合
            ctrl_collection_all = arm.collections.new(name=collection_name_all)
            ctrl_collection_first = arm.collections.new(name=collection_name_first)
            
            # 将所有控制骨骼添加到 "all" 集合
            for ctrl_bone_name in control_bone_names:
                bone = arm.bones.get(ctrl_bone_name)
                if bone:
                    ctrl_collection_all.assign(bone)
            
            # 将第一个控制骨骼添加到 "first" 集合
            first_ctrl_bone = arm.bones.get(control_bone_names[0])
            if first_ctrl_bone:
                ctrl_collection_first.assign(first_ctrl_bone)
                
            # 设置新创建的骨骼集合的初始可见性状态
            # 由于属性默认是show_all_ctrl_bones=True，所以显示所有
            ctrl_collection_all.is_visible = True
            ctrl_collection_first.is_visible = False
            
        except Exception as e:
            print(f"创建骨骼集合时出错: {e}")

        # 询问是否执行阻尼追踪
        context.window_manager.popup_menu(self.show_continue_dialog_damped, title="执行阻尼追踪?", icon='INFO')

        return {'FINISHED'}
    
    def show_continue_dialog_damped(self, menu, context):
        layout = menu.layout
        row = layout.row()
        row.label(text="是否继续执行阻尼追踪?", icon='QUESTION')
        
        row = layout.row()
        row.operator_context = 'EXEC_DEFAULT'
        row.operator("armature.apply_pose_setup", text="是", icon='CHECKMARK')
        
        row.operator_context = 'INVOKE_DEFAULT'
        row.operator("wm.close_panel", text="否", icon='X')

class ApplyPoseConstraintsOperator(bpy.types.Operator):
    bl_idname = "armature.apply_pose_setup"
    bl_label = "3.生成软骨绑定"
    bl_description = "应用所有姿态约束，包括复制旋转(FK)和软骨绑定，并设置驱动器"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'POSE' and context.object and context.object.type == 'ARMATURE'

    def execute(self, context):
        obj = context.object
        pose_bones = obj.pose.bones
        active_bone = context.active_bone
        if not active_bone: return {'CANCELLED'}

        # 从活动骨骼中提取基础名称部分，移除ctr_前缀并考虑可能的下划线后缀
        bone_name = active_bone.name.replace('ctr_', '')
        bone_name_parts = bone_name.split('.')
        if len(bone_name_parts) > 1 and bone_name_parts[-1].isdigit():
            # 如果骨骼名格式为 base_name.number，则取除了数字后缀的部分
            original_part = '.'.join(bone_name_parts[:-1])
        else:
            original_part = bone_name
            
        # 处理可能包含下划线的名称 (如 ctr_bone_1.001)
        base_parts = original_part.rsplit('_', 1)
        if len(base_parts) > 1 and base_parts[1].isdigit():
            # 如果名称包含下划线数字后缀，尝试找到匹配的名称模式
            potential_base = base_parts[0]
            # 检查是否这种命名模式存在，否则回退到原名
            matching_bones = [b for b in obj.data.bones if b.name.startswith(potential_base + '.')]
            if matching_bones:
                base_name = potential_base
            else:
                base_name = original_part
        else:
            base_name = original_part
            
        max_num = 0
        for bone in obj.data.bones:
            if bone.name.startswith(f"{base_name}.") and bone.name.split('.')[-1].isdigit() and int(bone.name.split('.')[-1]) > 0:
                num = int(bone.name.split('.')[-1])
                if num > max_num: max_num = num

        if max_num == 0: return {'CANCELLED'}

        # --- 1. FK Constraints ---
        for i in range(1, max_num + 1):
            def_bone = pose_bones.get(f"{base_name}.{i:03d}")
            if def_bone:
                for const in def_bone.constraints:
                    if const.type == 'COPY_ROTATION': def_bone.constraints.remove(const)
                const = def_bone.constraints.new('COPY_ROTATION')
                const.target, const.subtarget = obj, f"ctr_{base_name}.{i:03d}"

        # --- 2. Damped Track Constraints & Driver Setup ---
        constrained_bones = []
        last_numbered_bone = pose_bones.get(f"{base_name}.{max_num:03d}")
        if last_numbered_bone and obj.data.bones.get(f"{base_name}.000"):
            for const in last_numbered_bone.constraints:
                if const.type == 'DAMPED_TRACK': last_numbered_bone.constraints.remove(const)
            const = last_numbered_bone.constraints.new('DAMPED_TRACK')
            const.target, const.subtarget = obj, f"{base_name}.000"
            constrained_bones.append(last_numbered_bone)

        for i in range(max_num - 1, 0, -1):
            pose_bone = pose_bones.get(f"{base_name}.{i:03d}")
            if pose_bone:
                for const in pose_bone.constraints:
                    if const.type == 'DAMPED_TRACK': pose_bone.constraints.remove(const)
                const = pose_bone.constraints.new('DAMPED_TRACK')
                const.target, const.subtarget = obj, f"{base_name}.{i+1:03d}"
                constrained_bones.append(pose_bone)
        
        controller_bone = pose_bones.get(f"ctr_{base_name}.001")
        if controller_bone and constrained_bones:
            prop_name = "damped_track_influence"
            # 从偏好设置获取默认追踪强度值
            try:
                addon_prefs = context.preferences.addons.get(__name__ if __name__ != "__main__" else "damped_track_addon")
                if addon_prefs and hasattr(addon_prefs, 'preferences') and addon_prefs.preferences:
                    default_influence = addon_prefs.preferences.default_damped_track_influence
                else:
                    default_influence = 0.6
            except:
                default_influence = 0.6
            controller_bone.my_tool_props.damped_track_influence = default_influence
            for bone in constrained_bones:
                for const in bone.constraints:
                    if const.type == 'DAMPED_TRACK':
                        fcurve = const.driver_add("influence")
                        driver = fcurve.driver
                        driver.expression = "influence_var"
                        var = driver.variables.new()
                        var.name, var.type = "influence_var", 'SINGLE_PROP'
                        var.targets[0].id = obj
                        var.targets[0].data_path = f'pose.bones["{controller_bone.name}"].my_tool_props.{prop_name}'
        
        return {'FINISHED'}

def get_panel_class(category):
    # 根据类别创建唯一的面板ID
    panel_id = f"OBJECT_PT_damped_track_{category.lower().replace(' ', '_')}"
    
    class DampedTrackPanel(bpy.types.Panel):
        bl_label = "快速软骨绑定"
        bl_idname = panel_id
        bl_space_type = 'VIEW_3D'
        bl_region_type = 'UI'
        bl_category = category
    
        @classmethod
        def poll(cls, context):
            return (context.object and context.object.type == 'ARMATURE' and 
                    (context.mode == 'EDIT_ARMATURE' or context.mode == 'POSE'))

        def draw(self, context):
            layout = self.layout
            is_edit_mode = context.mode == 'EDIT_ARMATURE'
            is_pose_mode = context.mode == 'POSE'

            # 模式切换行 - 左中右对齐
            row = layout.row(align=True)
            row.scale_x = 1.5
            col = row.column()
            col.operator(WM_OT_SwitchObjectMode.bl_idname, text="对象", icon='OBJECT_DATAMODE')
            col = row.column()
            col.operator(WM_OT_SwitchEditMode.bl_idname, text="编辑", icon='EDITMODE_HLT')
            col = row.column()
            col.operator(WM_OT_SwitchPoseMode.bl_idname, text="姿态", icon='POSE_HLT')
            # 顶部添加刷新版本按钮
            col = row.column()
            col.operator(WM_OT_CheckAddonUpdate.bl_idname, text="刷新版本", icon='FILE_REFRESH')
            layout.separator()

            # 分割工具部分
            box = layout.box()
            box.label(text="1.骨骼分割工具", icon='MODIFIER')
            col = box.column()
            col.enabled = is_edit_mode
            row = col.row(align=True)
            row.prop(context.scene, "fib_segments")
            row.prop(context.scene, "fib_coefficient")
            row = col.row(align=True)
            row.operator(SubdivideFibOperator.bl_idname, icon='IPO_ELASTIC')
            row.operator(SubdivideAverageOperator.bl_idname, icon='MESH_GRID')
            
            layout.separator()

            # 绑定设置部分
            box = layout.box()
            box.label(text="绑定设置", icon='ARMATURE_DATA')
            row = box.row(align=True)
            fk_col = row.column()
            fk_col.enabled = is_edit_mode
            fk_col.operator(SetupControlRigOperator.bl_idname, icon='CON_FOLLOWPATH')
            
            dt_col = row.column()
            dt_col.enabled = is_pose_mode
            dt_col.operator(ApplyPoseConstraintsOperator.bl_idname, icon='CON_TRACKTO')
            # 刷新版本按钮已移动到顶部模式切换栏

            # 控制器属性部分（仅在姿态模式下且有活动骨骼时显示）
            if is_pose_mode and context.active_bone:
                layout.separator()
                try:
                    # 从活动骨骼中提取基础名称部分，移除ctr_前缀并考虑可能的下划线后缀
                    bone_name = context.active_bone.name.replace('ctr_', '')
                    bone_name_parts = bone_name.split('.')
                    if len(bone_name_parts) > 1 and bone_name_parts[-1].isdigit():
                        # 如果骨骼名格式为 base_name.number，则取除了数字后缀的部分
                        original_part = '.'.join(bone_name_parts[:-1])
                    else:
                        original_part = bone_name
                        
                    # 处理可能包含下划线的名称 (如 ctr_bone_1.001)
                    base_parts = original_part.rsplit('_', 1)
                    if len(base_parts) > 1 and base_parts[1].isdigit():
                        # 如果名称包含下划线数字后缀，尝试找到匹配的名称模式
                        potential_base = base_parts[0]
                        # 检查是否这种命名模式存在，否则回退到原名
                        matching_bones = [b for b in context.object.data.bones if b.name.startswith(potential_base + '.')]
                        if matching_bones:
                            base_name = potential_base
                        else:
                            base_name = original_part
                    else:
                        base_name = original_part
                        
                    controller_bone = context.object.pose.bones.get(f"ctr_{base_name}.001")
                    if controller_bone:
                        box = layout.box()
                        box.label(text="控制器属性", icon='PROPERTIES')
                        box.prop(controller_bone.my_tool_props, "damped_track_influence", slider=True)
                        box.prop(controller_bone.my_tool_props, "circle_scale", slider=True)
                        # 添加控制骨骼可见性选项
                        visibility_box = box.box()
                        
                        # 使用操作符按钮进行控制
                        row_btns = visibility_box.row()
                        op_show_hide_all = row_btns.operator("armature.toggle_show_all_ctrl_bones", text="控制骨", icon='HIDE_OFF')
                        op_toggle_first_only = row_btns.operator("armature.toggle_show_first_only_ctrl_bone", text="控制根骨", icon='HIDE_OFF')
                        # 去除状态显示行
                except Exception:
                    pass
    
    return DampedTrackPanel

def register():
    # 安全地添加自定义属性，避免重复添加
    if not hasattr(bpy.types.Scene, 'fib_segments'):
        bpy.types.Scene.fib_segments = bpy.props.IntProperty(name="段数", default=5, min=1)
    if not hasattr(bpy.types.Scene, 'fib_coefficient'):
        bpy.types.Scene.fib_coefficient = bpy.props.FloatProperty(name="系数", default=0.6)
    
    # 注册所有类，除了面板
    classes_to_register = [cls for cls in classes if cls.__name__ != 'DampedTrackPanel']
    for cls in classes_to_register:
        try:
            bpy.utils.register_class(cls)
        except RuntimeError:
            # 如果类已经注册，则跳过
            pass
    
    # 根据偏好设置实时注册面板
    show_in_n_panel = True
    show_in_tool_panel = False
    try:
        addon_prefs = bpy.context.preferences.addons.get(__name__)
        if addon_prefs and hasattr(addon_prefs, 'preferences') and addon_prefs.preferences:
            show_in_n_panel = getattr(addon_prefs.preferences, 'show_in_n_panel', True)
            show_in_tool_panel = getattr(addon_prefs.preferences, 'show_in_tool_panel', False)
    except Exception as e:
        print(f"读取偏好设置失败，使用默认值: {e}")

    apply_panel_prefs(show_in_n_panel, show_in_tool_panel)
    # 注册后强制重绘3D视图，避免需要切换其他选项才刷新
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
    except Exception as e:
        print(f"初始化重绘视图失败: {e}")
    
    if not hasattr(bpy.types.PoseBone, 'my_tool_props'):
        bpy.types.PoseBone.my_tool_props = bpy.props.PointerProperty(type=MyArmatureProperties)
    register_right_click_menu()

def unregister():
    unregister_right_click_menu()
    
    # 安全地删除自定义属性，如果它们存在
    if hasattr(bpy.types.Scene, 'fib_segments'):
        del bpy.types.Scene.fib_segments
    if hasattr(bpy.types.Scene, 'fib_coefficient'):
        del bpy.types.Scene.fib_coefficient
    if hasattr(bpy.types.PoseBone, 'my_tool_props'):
        del bpy.types.PoseBone.my_tool_props
    
    # 注销所有类，除了面板
    classes_to_register = [cls for cls in classes if cls.__name__ != 'DampedTrackPanel']
    for cls in reversed(classes_to_register):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            # 如果类未注册，则跳过
            pass
    
    # 注销动态创建的面板（确保清理）
    unregister_panel("Damped Track")
    unregister_panel("Tool")


# 定义一个子菜单（用于编辑模式）
class VIEW3D_MT_damped_track_edit_menu(bpy.types.Menu):
    bl_idname = "VIEW3D_MT_damped_track_edit_menu"
    bl_label = "快速软骨绑定"

    def draw(self, context):
        layout = self.layout
        # 显示模式切换按钮
        layout.operator(WM_OT_SwitchObjectMode.bl_idname, text="对象模式", icon='OBJECT_DATAMODE')
        layout.operator(WM_OT_SwitchPoseMode.bl_idname, text="姿态模式", icon='POSE_HLT')
        layout.separator()
        # 保留功能按钮
        layout.operator(SubdivideFibOperator.bl_idname, text="斐波那契细分", icon='IPO_ELASTIC')
        layout.operator(SubdivideAverageOperator.bl_idname, text="平均细分", icon='MESH_GRID')
        layout.operator(SetupControlRigOperator.bl_idname, text="生成FK绑定", icon='CON_FOLLOWPATH')


# 定义一个子菜单（用于姿态模式）
class VIEW3D_MT_damped_track_pose_menu(bpy.types.Menu):
    bl_idname = "VIEW3D_MT_damped_track_pose_menu"
    bl_label = "快速软骨绑定"

    def draw(self, context):
        layout = self.layout
        # 显示模式切换按钮
        layout.operator(WM_OT_SwitchObjectMode.bl_idname, text="对象模式", icon='OBJECT_DATAMODE')
        layout.operator(WM_OT_SwitchEditMode.bl_idname, text="编辑模式", icon='EDITMODE_HLT')
        layout.separator()
        # 保留功能按钮
        layout.operator(ApplyPoseConstraintsOperator.bl_idname, text="2.生成阻尼追踪", icon='CON_TRACKTO')


# 定义一个子菜单（用于对象模式）
class VIEW3D_MT_damped_track_object_menu(bpy.types.Menu):
    bl_idname = "VIEW3D_MT_damped_track_object_menu"
    bl_label = "快速软骨绑定"

    def draw(self, context):
        layout = self.layout
        # 显示模式切换按钮
        layout.operator(WM_OT_SwitchEditMode.bl_idname, text="编辑模式", icon='EDITMODE_HLT')
        layout.operator(WM_OT_SwitchPoseMode.bl_idname, text="姿态模式", icon='POSE_HLT')
        layout.separator()
        # 保留功能按钮
        layout.operator(SubdivideFibOperator.bl_idname, text="斐波那契细分", icon='IPO_ELASTIC')
        layout.operator(SubdivideAverageOperator.bl_idname, text="平均细分", icon='MESH_GRID')
        layout.operator(SetupControlRigOperator.bl_idname, text="生成FK绑定", icon='CON_FOLLOWPATH')
        layout.operator(ApplyPoseConstraintsOperator.bl_idname, text="生成阻尼追踪", icon='CON_TRACKTO')


# 添加对象模式右键菜单
def draw_object_context_menu(self, context):
    if context.active_object and context.active_object.type == 'ARMATURE' and is_right_click_menu_enabled():
        self.layout.separator()
        self.layout.menu(VIEW3D_MT_damped_track_object_menu.bl_idname)


# 添加编辑骨架模式右键菜单
def draw_edit_armature_context_menu(self, context):
    if context.mode == 'EDIT_ARMATURE' and context.active_object and context.active_object.type == 'ARMATURE' and is_right_click_menu_enabled():
        self.layout.separator()
        self.layout.menu(VIEW3D_MT_damped_track_edit_menu.bl_idname)


# 添加姿态模式右键菜单
def draw_pose_context_menu(self, context):
    if context.mode == 'POSE' and context.active_object and context.active_object.type == 'ARMATURE' and is_right_click_menu_enabled():
        self.layout.separator()
        self.layout.menu(VIEW3D_MT_damped_track_pose_menu.bl_idname)


# 注册右键菜单
def register_right_click_menu():
    # 由于偏好设置可能在插件加载时不可用，我们总是注册右键菜单
    # 但会在绘制菜单时根据偏好设置决定是否显示菜单项
    # 注册到对象上下文菜单
    bpy.types.VIEW3D_MT_object_context_menu.append(draw_object_context_menu)
    
    # 根据参考脚本，使用正确的菜单类型添加到编辑骨架右键菜单
    bpy.types.VIEW3D_MT_armature_context_menu.append(draw_edit_armature_context_menu)
    
    # 安全地注册到姿态上下文菜单
    if hasattr(bpy.types, 'VIEW3D_MT_pose_context_menu'):
        bpy.types.VIEW3D_MT_pose_context_menu.append(draw_pose_context_menu)


# 注销右键菜单
def unregister_right_click_menu():
    # 从对象上下文菜单注销
    bpy.types.VIEW3D_MT_object_context_menu.remove(draw_object_context_menu)
    
    # 从编辑骨架右键菜单注销
    bpy.types.VIEW3D_MT_armature_context_menu.remove(draw_edit_armature_context_menu)
    
    # 安全地从姿态上下文菜单注销
    if hasattr(bpy.types, 'VIEW3D_MT_pose_context_menu'):
        bpy.types.VIEW3D_MT_pose_context_menu.remove(draw_pose_context_menu)


# 辅助函数检查是否启用右键菜单
def is_right_click_menu_enabled():
    try:
        addon_prefs = bpy.context.preferences.addons.get(__name__)
        if addon_prefs and hasattr(addon_prefs, 'preferences') and addon_prefs.preferences:
            return addon_prefs.preferences.enable_right_click_menu
        else:
            return True  # 默认启用
    except:
        return True  # 出错时默认启用

# 用于关闭面板的操作符
class WM_OT_ClosePanel(bpy.types.Operator):
    bl_idname = "wm.close_panel"
    bl_label = "关闭面板"
    
    def execute(self, context):
        return {'FINISHED'}
class WM_OT_ToggleShowAllCtrlBones(bpy.types.Operator):
    bl_idname = "armature.toggle_show_all_ctrl_bones"
    bl_label = "切换显示所有控制骨骼"
    bl_description = "显示或隐藏所有控制骨骼"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        global _visibility_update_lock

        arm_obj = context.object
        if not arm_obj or arm_obj.type != 'ARMATURE':
            return {'CANCELLED'}
        armature = arm_obj.data

        # 确定要操作的基础名称：优先使用当前激活的控制骨骼，其次使用记录，再次自动扫描
        base_name = None
        pose_bone = getattr(context, 'active_pose_bone', None)
        if pose_bone and pose_bone.name.startswith('ctr_'):
            bone_name = pose_bone.name.replace('ctr_', '')
            parts = bone_name.split('.')
            if len(parts) > 1 and parts[-1].isdigit():
                base_name = '.'.join(parts[:-1])
            else:
                base_name = bone_name
            armature["last_ctrl_base_name"] = base_name
        else:
            base_name = armature.get("last_ctrl_base_name")

        if not base_name:
            # 扫描一个匹配的集合对
            for bc in armature.collections_all:
                cname = bc.name
                if cname.startswith("ctrl_") and cname.endswith("_all"):
                    bn = cname[len("ctrl_"):-len("_all")]
                    if f"ctrl_{bn}_first" in armature.collections_all:
                        base_name = bn
                        armature["last_ctrl_base_name"] = base_name
                        break

        if not base_name:
            self.report({'WARNING'}, "未找到可操作的控制集合")
            return {'CANCELLED'}

        collection_name_all = f"ctrl_{base_name}_all"
        collection_name_first = f"ctrl_{base_name}_first"

        # 使用 collections_all 以保证即使隐藏也可操作
        if not (collection_name_all in armature.collections_all and collection_name_first in armature.collections_all):
            self.report({'WARNING'}, "控制集合不存在")
            return {'CANCELLED'}

        all_collection = armature.collections_all[collection_name_all]
        first_collection = armature.collections_all[collection_name_first]

        # 切换显示/隐藏全部（不再修改属性，避免互斥逻辑干扰）
        if all_collection.is_visible:
            all_collection.is_visible = False
            first_collection.is_visible = False
        else:
            all_collection.is_visible = True
            first_collection.is_visible = False

        return {'FINISHED'}


class WM_OT_ToggleShowFirstOnlyCtrlBone(bpy.types.Operator):
    bl_idname = "armature.toggle_show_first_only_ctrl_bone"
    bl_label = "切换独显第一根控制骨骼"
    bl_description = "控制是否独显控制骨骼的根骨骼"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        global _visibility_update_lock

        arm_obj = context.object
        if not arm_obj or arm_obj.type != 'ARMATURE':
            return {'CANCELLED'}
        armature = arm_obj.data

        # 确定基础名称（与上一个操作符一致）
        base_name = None
        pose_bone = getattr(context, 'active_pose_bone', None)
        if pose_bone and pose_bone.name.startswith('ctr_'):
            bone_name = pose_bone.name.replace('ctr_', '')
            parts = bone_name.split('.')
            if len(parts) > 1 and parts[-1].isdigit():
                base_name = '.'.join(parts[:-1])
            else:
                base_name = bone_name
            armature["last_ctrl_base_name"] = base_name
        else:
            base_name = armature.get("last_ctrl_base_name")

        if not base_name:
            for bc in armature.collections_all:
                cname = bc.name
                if cname.startswith("ctrl_") and cname.endswith("_all"):
                    bn = cname[len("ctrl_"):-len("_all")]
                    if f"ctrl_{bn}_first" in armature.collections_all:
                        base_name = bn
                        armature["last_ctrl_base_name"] = base_name
                        break

        if not base_name:
            self.report({'WARNING'}, "未找到可操作的控制集合")
            return {'CANCELLED'}

        collection_name_all = f"ctrl_{base_name}_all"
        collection_name_first = f"ctrl_{base_name}_first"

        if not (collection_name_all in armature.collections_all and collection_name_first in armature.collections_all):
            self.report({'WARNING'}, "控制集合不存在")
            return {'CANCELLED'}

        all_collection = armature.collections_all[collection_name_all]
        first_collection = armature.collections_all[collection_name_first]

        # 切换独显/取消独显（不再修改属性）
        is_first_only = first_collection.is_visible and not all_collection.is_visible
        if is_first_only:
            all_collection.is_visible = True
            first_collection.is_visible = False
        else:
            all_collection.is_visible = False
            first_collection.is_visible = True

        return {'FINISHED'}

classes = (
    DampedTrackAddonPreferences,
    MyArmatureProperties,
    WM_OT_SwitchObjectMode,
    WM_OT_SwitchEditMode,
    WM_OT_SwitchPoseMode,
    SubdivideFibOperator,
    SubdivideAverageOperator,
    SetupControlRigOperator,
    ApplyPoseConstraintsOperator,
    WM_OT_CheckAddonUpdate,
    WM_OT_ToggleShowAllCtrlBones,
    WM_OT_ToggleShowFirstOnlyCtrlBone,
    WM_OT_ClosePanel,
    VIEW3D_MT_damped_track_edit_menu,
    VIEW3D_MT_damped_track_pose_menu,
    VIEW3D_MT_damped_track_object_menu,
)

if __name__ == "__main__":
    register()