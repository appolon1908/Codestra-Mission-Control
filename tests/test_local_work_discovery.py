from pathlib import Path
import subprocess
from mission_control.local_work_discovery import LocalWorkDiscovery

def git(p,*a):
 return subprocess.run(["git","-C",str(p),*a],check=True,text=True,capture_output=True).stdout.strip()

def test_discovers_recent_unpublished_and_dirty_work(tmp_path):
 root=tmp_path/"repos";root.mkdir();r=root/"Middleware-";r.mkdir()
 git(r,"init");git(r,"config","user.email","test@example.com");git(r,"config","user.name","Test")
 (r/"a.txt").write_text("one\n");git(r,"add","a.txt");git(r,"commit","-m","base")
 rows=LocalWorkDiscovery(root).scan("Middleware-",48)
 assert rows[0]["classification"]=="UNPUBLISHED_IMPLEMENTATION"
 (r/"a.txt").write_text("two\n")
 rows=LocalWorkDiscovery(root).scan("Middleware-",48)
 assert rows[0]["classification"]=="DIRTY_UNCLASSIFIED"
 assert rows[0]["dirty"]==1

def test_clean_detached_worktree_is_not_called_unpublished(tmp_path):
 root=tmp_path/"repos";root.mkdir();r=root/"Caddy";r.mkdir()
 git(r,"init");git(r,"config","user.email","test@example.com");git(r,"config","user.name","Test")
 (r/"a").write_text("x");git(r,"add","a");git(r,"commit","-m","base");git(r,"checkout","--detach")
 rows=LocalWorkDiscovery(root).scan("Caddy",48)
 assert rows[0]["classification"]=="REPRESENTED"
