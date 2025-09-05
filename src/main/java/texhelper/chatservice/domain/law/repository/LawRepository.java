package texhelper.chatservice.domain.law.repository;

import org.springframework.data.jpa.repository.JpaRepository;
import texhelper.chatservice.domain.law.entity.Law;

public interface LawRepository extends JpaRepository<Law, Long> {
}
