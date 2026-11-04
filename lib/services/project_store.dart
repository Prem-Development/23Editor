import 'dart:convert';
import 'dart:io';

import 'package:editor_23/models/project.dart';
import 'package:path_provider/path_provider.dart';

/// Local JSON project storage. No backend calls, no tracking: a single
/// projects file plus a renders directory under the app documents folder.
/// Pass [directory] in tests to isolate from the real store.
class ProjectStore {
  final Directory? directory;
  final Map<String, Project>? _memory;

  const ProjectStore({this.directory}) : _memory = null;

  /// In-memory store for widget tests (real file IO hangs inside
  /// testWidgets' fake async zone; file persistence is covered by
  /// project_store_test.dart running in real async).
  ProjectStore.memory([List<Project> seed = const <Project>[]])
    : directory = null,
      _memory = <String, Project>{
        for (final Project project in seed) project.id: project,
      };

  Future<Directory> _baseDir() async {
    final Directory? override = directory;
    if (override != null) {
      if (!override.existsSync()) {
        override.createSync(recursive: true);
      }
      return override;
    }
    final Directory docs = await getApplicationDocumentsDirectory();
    final Directory base = Directory('${docs.path}/23editor');
    if (!base.existsSync()) {
      base.createSync(recursive: true);
    }
    return base;
  }

  Future<Directory> rendersDir() async {
    final Directory base = await _baseDir();
    final Directory renders = Directory('${base.path}/renders');
    if (!renders.existsSync()) {
      renders.createSync(recursive: true);
    }
    return renders;
  }

  Future<File> _projectsFile() async {
    final Directory base = await _baseDir();
    return File('${base.path}/projects.json');
  }

  /// Newest first, capped at 50. Missing/corrupt files read as empty.
  Future<List<Project>> listProjects() async {
    final Map<String, Project>? memory = _memory;
    if (memory != null) {
      final List<Project> projects = memory.values.toList();
      projects.sort(
        (Project a, Project b) => b.updatedAt.compareTo(a.updatedAt),
      );
      return projects.take(50).toList();
    }
    try {
      final File file = await _projectsFile();
      if (!file.existsSync()) {
        return const <Project>[];
      }
      final Object? decoded = jsonDecode(await file.readAsString());
      if (decoded is! List) {
        return const <Project>[];
      }
      final List<Project> projects = decoded
          .whereType<Map<String, dynamic>>()
          .map(Project.fromJson)
          .where((Project project) => project.id.isNotEmpty)
          .toList();
      projects.sort(
        (Project a, Project b) => b.updatedAt.compareTo(a.updatedAt),
      );
      return projects.take(50).toList();
    } catch (_) {
      return const <Project>[];
    }
  }

  Future<Project?> getProject(String id) async {
    final List<Project> projects = await listProjects();
    for (final Project project in projects) {
      if (project.id == id) {
        return project;
      }
    }
    return null;
  }

  Future<Project> saveProject(Project project) async {
    final Map<String, Project>? memory = _memory;
    if (memory != null) {
      memory[project.id] = project;
      return project;
    }
    final List<Project> projects = (await listProjects()).toList();
    final int index = projects.indexWhere(
      (Project item) => item.id == project.id,
    );
    if (index >= 0) {
      projects[index] = project;
    } else {
      projects.insert(0, project);
    }
    final File file = await _projectsFile();
    await file.writeAsString(
      jsonEncode(projects.take(50).map((Project p) => p.toJson()).toList()),
    );
    return project;
  }
}
