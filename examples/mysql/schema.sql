create table sftp_users (
  username varchar(32) primary key,
  public_keys text,
  password_hash text,
  uid integer,
  gid integer,
  upload_dir varchar(255) not null default 'upload',
  comment text,
  disabled boolean not null default false
);

create table sftp_user_keys (
  username varchar(32) not null,
  name varchar(64) not null,
  public_key text not null,
  fingerprint varchar(128) not null,
  comment text,
  disabled boolean not null default false,
  created_at text,
  updated_at text,
  expires_at text,
  source text,
  metadata text,
  primary key (username, name),
  unique (fingerprint)
);
