import os
import zlib
import threading
import hashlib
import json
import flet as ft
import flet_permission_handler as fph
try:
    import bsdiff_cython
    bsdiff_diff = bsdiff_cython.diff
    BSDIFF_ENGINE_NAME = "bsdiff_cython (.pyx)"
except ImportError:
    try:
        import bsdiff4
        bsdiff_diff = bsdiff4.diff
        BSDIFF_ENGINE_NAME = "bsdiff4 (Nativo)"
    except ImportError:
        from bsdiff_compat import diff as bsdiff_diff
        BSDIFF_ENGINE_NAME = "BSDIFF4 Pure Python"

try:
    import bsdiff4
    bsdiff_patch = bsdiff4.patch
except ImportError:
    from bsdiff_compat import patch as bsdiff_patch

CHUNK_SIZE = 500 * 1024 * 1024  # 500 MB
PATCH_MAGIC = b"PMOBIE02"
PATCH_FOOTER_MAGIC = b"PMEND02!"
MAX_MANIFEST_SIZE = 16 * 1024 * 1024


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_exact(file, size, description):
    data = file.read(size)
    if len(data) != size:
        raise ValueError(f"Patch incompleto ou corrompido ({description}).")
    return data


def patch_output_path(base_dir, relative_path):
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("Patch possui um caminho de arquivo invalido.")
    relative_path = relative_path.replace("\\", "/")
    base_dir = os.path.abspath(base_dir)
    output_path = os.path.abspath(os.path.join(base_dir, *relative_path.split("/")))
    if os.path.commonpath([base_dir, output_path]) != base_dir:
        raise ValueError(f"Patch possui caminho fora da pasta alvo: {relative_path}")
    return output_path


def patch_relative_path(path):
    """Usa '/' dentro do patch, independentemente de Windows ou Android."""
    return path.replace("\\", "/") if isinstance(path, str) else path

def normalize_android_path(path):
    if not path:
        return path
    path = path.strip()
    # Converte caminhos do Android SAF para caminhos de sistema de arquivos reais
    if "/document/primary:" in path:
        path = path.replace("/document/primary:", "/storage/emulated/0/")
    elif "/tree/primary:" in path:
        path = path.replace("/tree/primary:", "/storage/emulated/0/")
    
    # Alguns pickers podem retornar caminhos codificados
    path = path.replace("%3A", ":").replace("%2F", "/")
    path = path.replace("\\", "/")
    
    return path

def create_patch(original_dir, modified_dir, patch_file, log_func, show_info, show_error):
    try:
        log_func(f"Iniciando criacao de patch ({BSDIFF_ENGINE_NAME})...")
        log_func(f"Patch: {patch_file}")
        
        if not os.path.exists(original_dir):
            show_error("Erro", "Pasta Original não existe!")
            return
        if not os.path.exists(modified_dir):
            show_error("Erro", "Pasta Modificada não existe!")
            return
            
        modified_files = set()
        for root, _, files in os.walk(modified_dir):
            for file_name in files:
                relative_path = patch_relative_path(os.path.relpath(os.path.join(root, file_name), modified_dir))
                modified_files.add(relative_path)
        
        log_func(f"Arquivos na pasta modificada: {len(modified_files)}")
        diffs_found = 0
        manifest_files = []
        manifest_paths = set()

        with open(patch_file, 'wb') as pf:
            pf.write(PATCH_MAGIC)
            for root, _, files in os.walk(original_dir):
                for file_name in files:
                    original_path = os.path.join(root, file_name)
                    relative_path = patch_relative_path(os.path.relpath(original_path, original_dir))
                    modified_path = os.path.join(modified_dir, *relative_path.split("/"))

                    if os.path.exists(modified_path):
                        log_func(f"Analisando: {relative_path}")
                        with open(original_path, 'rb') as f1, open(modified_path, 'rb') as f2:
                            chunk_idx = 0
                            while True:
                                o_chunk = f1.read(CHUNK_SIZE)
                                m_chunk = f2.read(CHUNK_SIZE)
                                if not o_chunk and not m_chunk:
                                    break
                                o_chunk = o_chunk or b""
                                m_chunk = m_chunk or b""
                                if o_chunk != m_chunk:
                                    if relative_path not in manifest_paths:
                                        manifest_files.append({
                                            "path": relative_path,
                                            "source_sha256": sha256_file(original_path),
                                            "target_sha256": sha256_file(modified_path),
                                            "target_size": os.path.getsize(modified_path),
                                        })
                                        manifest_paths.add(relative_path)
                                    log_func(f"  -> Diff detectado (P{chunk_idx}). Gerando delta...")
                                    delta = bsdiff_diff(o_chunk, m_chunk)
                                    compressed_delta = zlib.compress(delta, level=9)
                                    relative_path_data = relative_path.encode('utf-8')
                                    pf.write(len(relative_path_data).to_bytes(4, 'little'))
                                    pf.write(relative_path_data)
                                    pf.write((2).to_bytes(1, 'little'))
                                    pf.write(chunk_idx.to_bytes(4, 'little'))
                                    pf.write(len(compressed_delta).to_bytes(4, 'little'))
                                    pf.write(compressed_delta)
                                    diffs_found += 1
                                chunk_idx += 1
                                import gc
                                gc.collect()
                        modified_files.discard(relative_path)

            for relative_path in modified_files:
                modified_path = os.path.join(modified_dir, *relative_path.split("/"))
                manifest_files.append({
                    "path": relative_path,
                    "source_sha256": None,
                    "target_sha256": sha256_file(modified_path),
                    "target_size": os.path.getsize(modified_path),
                })
                log_func(f"Novo arquivo: {relative_path}")
                with open(modified_path, 'rb') as f:
                    chunk_idx = 0
                    while True:
                        data = f.read(CHUNK_SIZE)
                        if not data:
                            break
                        compressed_data = zlib.compress(data, level=9)
                        relative_path_data = relative_path.encode('utf-8')
                        pf.write(len(relative_path_data).to_bytes(4, 'little'))
                        pf.write(relative_path_data)
                        pf.write((3).to_bytes(1, 'little'))
                        pf.write(chunk_idx.to_bytes(4, 'little'))
                        pf.write(len(compressed_data).to_bytes(4, 'little'))
                        pf.write(compressed_data)
                        log_func(f"  -> Adicionado (P{chunk_idx})")
                        diffs_found += 1
                        import gc
                        gc.collect()
                        chunk_idx += 1

            manifest_data = json.dumps(
                {"version": 2, "files": manifest_files},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            pf.write(manifest_data)
            pf.write(len(manifest_data).to_bytes(4, "little"))
            pf.write(PATCH_FOOTER_MAGIC)
        
        if diffs_found > 0:
            show_info("Sucesso", f"Patch criado com {diffs_found} alteracoes!")
        else:
            show_info("Aviso", "Nenhuma diferença encontrada entre as pastas.")
    except Exception as e:
        show_error("Erro", f"Erro: {e}")

def _apply_patch_legacy(original_dir, patch_file, log_func, show_info, show_error):
    try:
        log_func(f"Iniciando aplicacão de patch...")
        log_func(f"Arquivo: {patch_file}")
        
        if not os.path.exists(patch_file):
            show_error("Erro", "Patch não encontrado!")
            return
        with open(patch_file, 'rb') as pf:
            while True:
                path_size_bytes = pf.read(4)
                if not path_size_bytes:
                    break
                path_size = int.from_bytes(path_size_bytes, 'little')
                relative_path = pf.read(path_size).decode('utf-8')
                patch_type = int.from_bytes(pf.read(1), 'little')
                output_path = os.path.join(original_dir, relative_path)
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                chunk_idx = int.from_bytes(pf.read(4), 'little')
                data_size = int.from_bytes(pf.read(4), 'little')
                compressed_data = pf.read(data_size)
                data = zlib.decompress(compressed_data)
                del compressed_data

                if patch_type == 2:
                    mode = 'r+b' if os.path.exists(output_path) else 'wb'
                    with open(output_path, mode) as f:
                        f.seek(chunk_idx * CHUNK_SIZE)
                        o_chunk = f.read(CHUNK_SIZE) or b""
                        m_chunk = bsdiff_patch(o_chunk, data)
                        f.seek(chunk_idx * CHUNK_SIZE)
                        f.write(m_chunk)
                    import gc
                    del o_chunk, m_chunk, data
                    gc.collect()
                    log_func(f"Atualizado: {relative_path} (P{chunk_idx})")
                elif patch_type == 3:
                    mode = 'r+b' if os.path.exists(output_path) else 'wb'
                    with open(output_path, mode) as f:
                        f.seek(chunk_idx * CHUNK_SIZE)
                        f.write(data)
                    import gc
                    del data
                    gc.collect()
                    log_func(f"Criado: {relative_path} (P{chunk_idx})")
        show_info("Sucesso", "Patch aplicado!")
    except Exception as e:
        show_error("Erro", f"Erro: {e}")

def apply_patch(original_dir, patch_file, log_func, show_info, show_error):
    """Aplica somente patches v2 cuja base inteira tenha sido validada."""
    try:
        log_func("Verificando integridade do patch e da pasta alvo...")
        if not os.path.isdir(original_dir):
            show_error("Erro", "Pasta alvo nao existe!")
            return
        if not os.path.isfile(patch_file):
            show_error("Erro", "Patch nao encontrado!")
            return

        with open(patch_file, "rb") as pf:
            if read_exact(pf, len(PATCH_MAGIC), "cabecalho") != PATCH_MAGIC:
                raise ValueError("Patch antigo ou invalido. Crie um novo patch com esta versao do app.")

            pf.seek(0, os.SEEK_END)
            patch_size = pf.tell()
            footer_size = 4 + len(PATCH_FOOTER_MAGIC)
            if patch_size < len(PATCH_MAGIC) + footer_size:
                raise ValueError("Patch incompleto ou corrompido.")
            pf.seek(-footer_size, os.SEEK_END)
            manifest_size = int.from_bytes(read_exact(pf, 4, "tamanho do manifesto"), "little")
            if read_exact(pf, len(PATCH_FOOTER_MAGIC), "rodape") != PATCH_FOOTER_MAGIC:
                raise ValueError("Patch invalido: rodape nao encontrado.")
            manifest_start = patch_size - footer_size - manifest_size
            if manifest_size > MAX_MANIFEST_SIZE or manifest_start < len(PATCH_MAGIC):
                raise ValueError("Patch invalido: manifesto com tamanho incorreto.")
            pf.seek(manifest_start)
            manifest = json.loads(read_exact(pf, manifest_size, "manifesto").decode("utf-8"))

            if manifest.get("version") != 2 or not isinstance(manifest.get("files"), list):
                raise ValueError("Patch invalido: manifesto nao suportado.")

            files = {}
            for info in manifest["files"]:
                if not isinstance(info, dict):
                    raise ValueError("Patch invalido: entrada de manifesto incorreta.")
                relative_path = patch_relative_path(info.get("path"))
                output_path = patch_output_path(original_dir, relative_path)
                source_hash = info.get("source_sha256")
                target_hash = info.get("target_sha256")
                target_size = info.get("target_size")
                if relative_path in files or not isinstance(target_hash, str) or len(target_hash) != 64:
                    raise ValueError("Patch invalido: hashes ou caminhos incorretos.")
                if source_hash is not None and (not isinstance(source_hash, str) or len(source_hash) != 64):
                    raise ValueError("Patch invalido: hash da base incorreto.")
                if not isinstance(target_size, int) or target_size < 0:
                    raise ValueError("Patch invalido: tamanho final incorreto.")
                files[relative_path] = (output_path, source_hash, target_hash, target_size)

            mismatches = []
            for relative_path, (output_path, source_hash, _, _) in files.items():
                if source_hash is None:
                    if os.path.exists(output_path):
                        mismatches.append(f"{relative_path} (o arquivo novo ja existe)")
                elif not os.path.isfile(output_path) or sha256_file(output_path) != source_hash:
                    mismatches.append(relative_path)
            if mismatches:
                preview = "\n".join(mismatches[:8])
                extra = "" if len(mismatches) <= 8 else f"\n... e mais {len(mismatches) - 8} arquivo(s)."
                show_error("Patch nao aplicado", "A pasta alvo nao corresponde a base do patch:\n" + preview + extra)
                return

            log_func("Base validada. Aplicando patch...")
            pf.seek(len(PATCH_MAGIC))
            while pf.tell() < manifest_start:
                path_size = int.from_bytes(read_exact(pf, 4, "tamanho do caminho"), "little")
                if path_size == 0 or path_size > 1024 * 1024:
                    raise ValueError("Patch invalido: tamanho de caminho incorreto.")
                relative_path = patch_relative_path(
                    read_exact(pf, path_size, "caminho").decode("utf-8")
                )
                patch_type = int.from_bytes(read_exact(pf, 1, "tipo"), "little")
                chunk_idx = int.from_bytes(read_exact(pf, 4, "indice do bloco"), "little")
                data_size = int.from_bytes(read_exact(pf, 4, "tamanho dos dados"), "little")
                if data_size > manifest_start - pf.tell():
                    raise ValueError("Patch incompleto ou corrompido (dados).")
                data = zlib.decompress(read_exact(pf, data_size, "dados"))

                if relative_path not in files:
                    raise ValueError(f"Patch invalido: arquivo sem manifesto: {relative_path}")
                output_path, source_hash, _, _ = files[relative_path]
                if patch_type == 2 and source_hash is not None:
                    with open(output_path, "r+b") as file:
                        file.seek(chunk_idx * CHUNK_SIZE)
                        original_chunk = file.read(CHUNK_SIZE)
                        file.seek(chunk_idx * CHUNK_SIZE)
                        file.write(bsdiff_patch(original_chunk, data))
                elif patch_type == 3 and source_hash is None:
                    os.makedirs(os.path.dirname(output_path), exist_ok=True)
                    mode = "r+b" if os.path.exists(output_path) else "wb"
                    with open(output_path, mode) as file:
                        file.seek(chunk_idx * CHUNK_SIZE)
                        file.write(data)
                else:
                    raise ValueError(f"Patch invalido: tipo incorreto para {relative_path}")

            for relative_path, (output_path, _, target_hash, target_size) in files.items():
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                if not os.path.exists(output_path):
                    open(output_path, "wb").close()
                with open(output_path, "r+b") as file:
                    file.truncate(target_size)
                if sha256_file(output_path) != target_hash:
                    raise ValueError(f"Falha na verificacao final: {relative_path}")
                log_func(f"Verificado: {relative_path}")

        show_info("Sucesso", "Patch aplicado e verificado com SHA-256!")
    except Exception as e:
        show_error("Erro", f"Erro ao aplicar patch: {e}")


def main(page: ft.Page):
    page.title = "Patcher Mobile"
    page.window.width = 680
    page.window.height = 650
    page.theme_mode = ft.ThemeMode.DARK

    def show_info(title, message):
        dlg = ft.AlertDialog(title=ft.Text(title), content=ft.Text(message))
        page.open(dlg)

    def show_error(title, message):
        dlg = ft.AlertDialog(title=ft.Text(title), content=ft.Text(message))
        page.open(dlg)

    permission_handler = None

    def request_storage_access(_=None):
        if page.platform != ft.PagePlatform.ANDROID or permission_handler is None:
            return
        try:
            # No Android 11+, esta chamada abre a tela especial "Acesso a todos
            # os arquivos". Ela nao aparece na lista comum de permissoes do app.
            permission_handler.request_permission(
                fph.PermissionType.MANAGE_EXTERNAL_STORAGE
            )
        except Exception as e:
            show_error(
                "Permissao de armazenamento",
                "Nao foi possivel abrir a tela de acesso a todos os arquivos: " + str(e),
            )

    def show_help(_):
        help_text = (
            "Instrucoes de Uso:\n\n"
            "1. Permissoes (Android):\n"
            "- O Android requer permissao de armazenamento.\n"
            "- Se o app falhar com 'Permission Denied', va em:\n"
            "  Configuracoes > Apps > Patch Maker > Permissoes\n"
            "  e ative 'Acesso a todos os arquivos'.\n\n"
            "2. Criar Patch:\n"
            "- Selecione a pasta original e a pasta modificada.\n"
            "- Escolha onde salvar o arquivo de patch.\n"
            "- Clique em 'Criar Patch' para gerar o delta.\n\n"
            "3. Aplicar Patch:\n"
            "- Selecione a pasta original e o arquivo de patch.\n"
            "- Clique em 'Aplicar Patch' para aplicar as mudancas.\n\n"
            "Nota: Faca backup dos seus arquivos antes de aplicar patches."
        )
        dlg = ft.AlertDialog(
            title=ft.Text("Ajuda"),
            content=ft.Text(help_text),
            actions=[ft.TextButton("Fechar", on_click=lambda e: page.close(dlg))]
        )
        page.open(dlg)

    log_list = ft.ListView(expand=True, spacing=5, auto_scroll=True)

    def log_func(message: str):
        log_list.controls.append(ft.Text(message, size=12, color=ft.Colors.GREEN_400))
        if len(log_list.controls) > 7:
            del log_list.controls[0]
        page.update()

    def clear_log(_):
        log_list.controls.clear()
        log_func("Log limpo!")

    file_picker = ft.FilePicker()
    page.overlay.append(file_picker)
    selection_type = [""]

    def on_picker_result(e: ft.FilePickerResultEvent):
        res = e.path if e.path else (e.files[0].path if e.files else None)
        if res:
            res = normalize_android_path(res)
            if selection_type[0] == 'c_orig':  c_orig_field.value = res
            elif selection_type[0] == 'c_mod':  c_mod_field.value = res
            elif selection_type[0] == 'c_patch': c_patch_field.value = res
            elif selection_type[0] == 'a_orig':  a_orig_field.value = res
            elif selection_type[0] == 'a_patch': a_patch_field.value = res
            page.update()

    file_picker.on_result = on_picker_result

    # Campos - Criar Patch
    c_orig_field  = ft.TextField(label="Pasta Original",   expand=True)
    c_mod_field   = ft.TextField(label="Pasta Modificada", expand=True)
    c_patch_field = ft.TextField(label="Salvar Patch em", expand=True)

    # Campos - Aplicar Patch
    a_orig_field  = ft.TextField(label="Pasta Alvo",    expand=True)
    a_patch_field = ft.TextField(label="Arquivo Patch", expand=True)

    tabs = ft.Tabs(
        selected_index=0,
        animation_duration=300,
        tabs=[
            ft.Tab(
                text="Criar Patch",
                icon=ft.Icons.CREATE_NEW_FOLDER,
                content=ft.Container(
                    padding=20,
                    expand=True,
                    content=ft.Column([
                        ft.Row([ft.Text("Original:",  width=80), c_orig_field,  ft.ElevatedButton("...", on_click=lambda _: (selection_type.clear(), selection_type.append('c_orig'),  file_picker.get_directory_path()))]),
                        ft.Row([ft.Text("Modificada:", width=80), c_mod_field,  ft.ElevatedButton("...", on_click=lambda _: (selection_type.clear(), selection_type.append('c_mod'),   file_picker.get_directory_path()))]),
                        ft.Row([ft.Text("Patch:",      width=80), c_patch_field, ft.ElevatedButton("...", on_click=lambda _: (selection_type.clear(), selection_type.append('c_patch'), file_picker.save_file()))]),
                        ft.Row([
                            ft.ElevatedButton("Criar Patch", icon=ft.Icons.AUTO_FIX_HIGH, bgcolor=ft.Colors.GREEN_700, on_click=lambda _: threading.Thread(target=create_patch, args=(c_orig_field.value, c_mod_field.value, c_patch_field.value, log_func, show_info, show_error), daemon=True).start()),
                        ], alignment="center"),
                    ], spacing=20, expand=True, scroll=ft.ScrollMode.ADAPTIVE)
                )
            ),
            ft.Tab(
                text="Aplicar Patch",
                icon=ft.Icons.SYSTEM_UPDATE_ALT,
                content=ft.Container(
                    padding=20,
                    expand=True,
                    content=ft.Column([
                        ft.Row([ft.Text("Pasta:",      width=80), a_orig_field,  ft.ElevatedButton("...", on_click=lambda _: (selection_type.clear(), selection_type.append('a_orig'),  file_picker.get_directory_path()))]),
                        ft.Row([ft.Text("Patch:",      width=80), a_patch_field, ft.ElevatedButton("...", on_click=lambda _: (selection_type.clear(), selection_type.append('a_patch'), file_picker.pick_files()))]),
                        ft.Row([
                            ft.ElevatedButton("Aplicar Patch", icon=ft.Icons.PLAY_ARROW, bgcolor=ft.Colors.BLUE_700, on_click=lambda _: threading.Thread(target=apply_patch, args=(a_orig_field.value, a_patch_field.value, log_func, show_info, show_error), daemon=True).start()),
                        ], alignment="center"),
                    ], spacing=20, expand=True, scroll=ft.ScrollMode.ADAPTIVE)
                )
            ),
        ],
        expand=True
    )

    page.add(
        ft.Column([
            ft.Row([
                ft.Text("Patcher Mobile", size=24, weight="bold"),
                ft.Row([
                    ft.IconButton(
                        ft.Icons.FOLDER_SHARED,
                        tooltip="Liberar acesso a todos os arquivos",
                        on_click=request_storage_access,
                    ),
                    ft.IconButton(ft.Icons.HELP_OUTLINE, on_click=show_help),
                    ft.IconButton(ft.Icons.DELETE_SWEEP, tooltip="Limpar Log", on_click=clear_log)
                ])
            ], alignment="spaceBetween"),
            tabs,
            ft.Divider(),
            ft.Text("Relatorio:", size=14, weight="bold"),
            ft.Container(
                content=log_list,
                height=150,
                border_radius=5,
                border=ft.border.all(1, ft.Colors.BLUE_GREY_700),
                padding=5,
            )
        ], expand=True)
    )

    # Adiciona o Gerenciador de Permissões apenas em plataformas mobile suportadas (Android/iOS)
    if page.platform == ft.PagePlatform.ANDROID:
        try:
            permission_handler = fph.PermissionHandler()
            page.overlay.append(permission_handler)
            page.update()
            request_storage_access()
        except Exception as e:
            print(f"Erro ao solicitar permissao avançada: {e}")

if __name__ == "__main__":
    ft.app(main)
