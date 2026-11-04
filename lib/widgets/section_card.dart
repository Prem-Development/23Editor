import 'package:editor_23/theme/app_theme.dart';
import 'package:flutter/material.dart';

/// Rounded card that groups one settings section with a title and helper.
class SectionCard extends StatelessWidget {
  final String title;
  final IconData? icon;
  final String? helper;
  final Widget child;

  const SectionCard({
    super.key,
    required this.title,
    this.icon,
    this.helper,
    required this.child,
  });

  @override
  Widget build(BuildContext context) {
    final TextTheme text = Theme.of(context).textTheme;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            Row(
              children: <Widget>[
                if (icon != null) ...<Widget>[
                  Icon(icon, size: 20, color: AppColors.violetSoft),
                  const SizedBox(width: 8),
                ],
                Expanded(child: Text(title, style: text.titleMedium)),
              ],
            ),
            const SizedBox(height: 12),
            child,
            if (helper != null) ...<Widget>[
              const SizedBox(height: 10),
              Text(helper!, style: text.bodySmall),
            ],
          ],
        ),
      ),
    );
  }
}
