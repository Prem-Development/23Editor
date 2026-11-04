import 'package:editor_23/theme/app_theme.dart';
import 'package:editor_23/widgets/primary_button.dart';
import 'package:flutter/material.dart';

/// Honest placeholder shown when there is nothing to list yet.
class EmptyState extends StatelessWidget {
  final IconData icon;
  final String title;
  final String message;
  final String? actionLabel;
  final VoidCallback? onAction;

  const EmptyState({
    super.key,
    required this.icon,
    required this.title,
    required this.message,
    this.actionLabel,
    this.onAction,
  });

  @override
  Widget build(BuildContext context) {
    final TextTheme text = Theme.of(context).textTheme;
    final String? label = actionLabel;
    final VoidCallback? action = onAction;
    return Card(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 28),
        child: Column(
          children: <Widget>[
            Container(
              padding: const EdgeInsets.all(16),
              decoration: const BoxDecoration(
                color: AppColors.surfaceHigh,
                shape: BoxShape.circle,
              ),
              child: Icon(icon, size: 32, color: AppColors.violetSoft),
            ),
            const SizedBox(height: 16),
            Text(title, style: text.titleLarge, textAlign: TextAlign.center),
            const SizedBox(height: 8),
            Text(message, style: text.bodyMedium, textAlign: TextAlign.center),
            if (label != null && action != null) ...<Widget>[
              const SizedBox(height: 20),
              PrimaryButton(
                label: label,
                icon: Icons.video_call_outlined,
                onPressed: action,
              ),
            ],
          ],
        ),
      ),
    );
  }
}
