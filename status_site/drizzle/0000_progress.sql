CREATE TABLE `snapshots` (`id` text PRIMARY KEY NOT NULL, `payload` text NOT NULL, `received_at` text NOT NULL);
--> statement-breakpoint
CREATE TABLE `subscriptions` (`id` text PRIMARY KEY NOT NULL, `payload` text NOT NULL, `created_at` text NOT NULL, `test_requested_at` text);
