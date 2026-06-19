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
