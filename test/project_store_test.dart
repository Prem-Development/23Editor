import 'dart:io';

import 'package:editor_23/models/edit_setup.dart';
import 'package:editor_23/models/project.dart';
import 'package:editor_23/services/project_store.dart';
import 'package:flutter_test/flutter_test.dart';

Project _project(String id) {
  return Project(
    id: id,
    name: 'clip.mp4',
    sourcePath: '/videos/clip.mp4',
    fileName: 'clip.mp4',
    instruction: 'keep the first 5 seconds',
    style: EditStyle.cinematic,
    aspectRatio: AspectRatioOption.vertical,
    fileId: 'f1',
    plan: <String, dynamic>{
      'operations': <dynamic>[
        <String, dynamic>{'op': 'trim', 'start': 0.0, 'end': 5.0},
      ],
    },
    planModel: 'muse-spark-1.3',
    renderPath: '/renders/out.mp4',
    renderDurationSec: 5.0,
    steps: const <String>['plan_validated', 'ffmpeg_render'],
    status: 'rendered',
    updatedAt: DateTime.utc(2026, 1, 2, 3, 4, 5),
  );
}

void main() {
  test('project round-trips through JSON', () {
    final Project restored = Project.fromJson(_project('a').toJson());
    expect(restored.id, 'a');
    expect(restored.instruction, 'keep the first 5 seconds');
    expect(restored.style, EditStyle.cinematic);
    expect(restored.aspectRatio, AspectRatioOption.vertical);
    expect(restored.fileId, 'f1');
    expect(restored.planModel, 'muse-spark-1.3');
    expect(restored.renderPath, '/renders/out.mp4');
    expect(restored.renderDurationSec, 5.0);
    expect(restored.steps, <String>['plan_validated', 'ffmpeg_render']);
    expect(restored.status, 'rendered');
  });

  test('store saves, lists newest first, and updates', () async {
    final Directory dir = await Directory.systemTemp.createTemp(
      'project-store-test',
    );
    final ProjectStore store = ProjectStore(directory: dir);
    expect(await store.listProjects(), isEmpty);

    await store.saveProject(_project('old'));
    await store.saveProject(
      _project('new').copyWith(updatedAt: DateTime.utc(2026, 6, 1)),
    );
    final List<Project> projects = await store.listProjects();
    expect(projects.map((Project p) => p.id).toList(), <String>['new', 'old']);

    await store.saveProject(
      _project(
        'old',
      ).copyWith(status: 'planned', updatedAt: DateTime.utc(2026, 7, 1)),
    );
    final List<Project> updated = await store.listProjects();
    expect(updated.map((Project p) => p.id).toList(), <String>['old', 'new']);
    expect(await store.getProject('old'), isNotNull);
    expect((await store.getProject('old'))!.status, 'planned');
    expect(await store.getProject('missing'), isNull);
  });
}
